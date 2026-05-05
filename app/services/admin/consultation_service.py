"""
Admin consultation-monitor service layer.

Responsibilities
----------------
1. Page the ``symptom_logs`` table with optional disease / triage /
   language filters and project each row into a compact admin shape.
2. Expand a single log_id into a rich detail payload by parsing the
   JSON blob written at prediction time
   (``prediction_service._persist_log``):

       {
         "language": "...",
         "triage_rule": "...",
         "stub_mode": ...,
         "top_conditions": [...],   # full TopCondition dumps (Top-3)
         "extracted_symptoms": [...],
         "explanation": {...},      # rationale / key_symptoms / SHAP-style
         "metadata": {...}          # age / sex / severity / chronic ...
       }

   The blob may be missing or malformed on legacy / stub-mode rows; we
   degrade gracefully (empty fields) instead of erroring out.
"""
from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import ConsultationFeedback, SymptomLog, User
from app.models.admin import (
    AdminConsultationDetail,
    AdminConsultationListItem,
    AdminConsultationListResponse,
    AdminShapSummary,
    AdminTopPrediction,
)
from app.repositories.admin import consultation_admin_repo

logger = get_logger(__name__)

_PREVIEW_MAX_CHARS = 140
_TOP_K = 3


# --------------------------------------------------------------------------- #
# List                                                                         #
# --------------------------------------------------------------------------- #
async def list_consultations(
    db: AsyncSession,
    *,
    disease: str | None,
    triage: str | None,
    language: str | None,
    limit: int,
    offset: int,
) -> AdminConsultationListResponse:
    rows, total = await consultation_admin_repo.list_consultations(
        db,
        disease=disease,
        triage=triage,
        language=language,
        limit=limit,
        offset=offset,
    )

    items: list[AdminConsultationListItem] = []
    for log, user in rows:
        log = cast(SymptomLog, log)
        user = cast(User | None, user)
        items.append(_to_list_item(log, user))

    has_more = (offset + len(items)) < total
    return AdminConsultationListResponse(
        items=items,
        total=total,
        has_more=has_more,
        limit=limit,
        offset=offset,
        filters={"disease": disease, "triage": triage, "language": language},
    )


# --------------------------------------------------------------------------- #
# Detail                                                                       #
# --------------------------------------------------------------------------- #
async def get_consultation_detail(
    db: AsyncSession, *, log_id: int,
) -> AdminConsultationDetail:
    found = await consultation_admin_repo.get_consultation_detail(
        db, log_id=log_id,
    )
    if found is None:
        raise NotFoundError(
            message="Consultation not found",
            code="admin_consultation_not_found",
        )

    log, user, feedback = found
    blob = _safe_load_blob(log.explanation_json)

    top_predictions = _project_top_predictions(blob)
    explanation = blob.get("explanation") or {}
    metadata = blob.get("metadata") or {}

    return AdminConsultationDetail(
        log_id=log.log_id,
        user_id=log.user_id,
        username=user.username if user else None,
        raw_text=log.raw_text or "",
        language=blob.get("language") or (user.language_pref if user else None),
        triage_level=log.triage_level,
        triage_rule=blob.get("triage_rule"),
        extracted_symptoms=list(blob.get("extracted_symptoms") or []),
        top_predictions=top_predictions,
        recommended_specialist=(
            top_predictions[0].specialist_type if top_predictions else None
        ),
        shap_summary=AdminShapSummary(
            rationale=explanation.get("rationale"),
            key_symptoms=list(explanation.get("key_symptoms") or []),
            top_features=list(explanation.get("feature_importance") or []),
            confidence_breakdown=explanation.get("confidence_breakdown"),
        ),
        created_at=log.created_at,
        feedback_rating=feedback.helpfulness_rating if feedback else None,
        feedback_text=feedback.feedback_text if feedback else None,
    )


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #
def _to_list_item(log: SymptomLog, user: User | None) -> AdminConsultationListItem:
    language: str | None = None
    if user is not None and user.language_pref:
        language = user.language_pref
    elif log.user_id is None:
        # Guests are forced to English by prediction_service.
        language = "English"

    return AdminConsultationListItem(
        log_id=log.log_id,
        user_id=log.user_id,
        username=user.username if user else None,
        main_condition=log.predicted_condition,
        confidence_score=log.confidence_score,
        triage_level=log.triage_level,
        language=language,
        raw_text_preview=_preview(log.raw_text),
        created_at=log.created_at,
    )


def _project_top_predictions(blob: dict[str, Any]) -> list[AdminTopPrediction]:
    """Pull at most Top-3 predictions out of the stored bundle."""
    raw = blob.get("top_conditions") or []
    out: list[AdminTopPrediction] = []
    for entry in raw[:_TOP_K]:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                AdminTopPrediction(
                    name_en=str(entry.get("name_en") or entry.get("name") or ""),
                    confidence=float(entry.get("confidence") or 0.0),
                    specialist_type=entry.get("specialist_type"),
                )
            )
        except (TypeError, ValueError):
            logger.debug("Skipping malformed top_condition entry: %r", entry)
    return out


def _safe_load_blob(raw: str | None) -> dict[str, Any]:
    """Robust ``explanation_json`` loader — never raises on malformed rows."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("Admin: failed to parse explanation_json: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _preview(text: str | None) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= _PREVIEW_MAX_CHARS:
        return cleaned
    return cleaned[: _PREVIEW_MAX_CHARS - 1].rstrip() + "\u2026"
