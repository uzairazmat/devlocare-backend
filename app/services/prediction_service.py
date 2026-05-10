"""
UC-01 / UC-03 / UC-04 / UC-05 — symptom-prediction service-layer orchestrator.

Stateful chat flow
------------------
The service supports a multi-turn conversation pinned to a single
``symptom_logs`` row. Each ``POST /predict/text`` call:

    1. Looks up the row by ``payload.log_id`` (or creates a fresh one).
    2. Appends the new user message to ``chat_history``.
    3. Concatenates every prior ``user`` turn into one combined string and
       runs the classifier on it.
    4. Branches on confidence:

        * **Below threshold** — picks a random clarifying question from
          ``app.utils.followups``, appends it as the assistant turn,
          persists, and returns ``requires_followup=True`` (no triage / no
          LIME yet).
        * **At or above threshold** — runs triage + KB enrichment + LIME
          explanation, finalises the row, appends the closing assistant
          message, and returns the full ``PredictionResponse``.

End-to-end finalisation flow
----------------------------
    1. Resolve target language (UC-05)
    2. NLP preprocessing  — `preprocess_service.clean_text`
    3. Symptom extraction — `preprocess_service.extract_symptoms`
    4. ML prediction      — `ml_client.predict_top_k` (Top-3, calibrated probs)
    5. KB enrichment      — `kb_service.bulk_lookup`
    6. Smart triage (UC-03)
    7. Explainability (UC-04) — `ExplainService.get_explanation`
    8. Persist log (user_id may be None for guests)
    9. Return PredictionResponse
"""
from __future__ import annotations

import json
from typing import Any, Iterable, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import ml_client
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import SymptomLog, User
from app.models.request import SymptomTextRequest
from app.models.response import (
    CareTipsBilingual,
    ChatTurn,
    ConfidenceStatus,
    Explanation,
    FeatureImportance,
    LanguageLiteral,
    PredictionResponse,
    TopCondition,
    TriageLevel,
    get_disclaimer,
)
from app.repositories import log_repo
from app.services import kb_service, preprocess_service
from app.services.explain_service import get_explain_service
from app.services.kb_service import LocalizedDisease
from app.utils.followups import pick_followup_question

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Triage rule configuration                                                    #
# --------------------------------------------------------------------------- #
_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "chest pain",
    "shortness of breath",
    "difficulty breathing",
    "severe bleeding",
    "high fever",
    "confusion",
    "unconscious",
    "severe abdominal pain",
)

_PREGNANCY_ESCALATION_KEYWORDS: tuple[str, ...] = (
    "bleeding", "severe pain", "abdominal pain", "fever",
)

_AGE_VULNERABLE_LOWER = 5
_AGE_VULNERABLE_UPPER = 65

_FINAL_ASSISTANT_MESSAGE = "I have enough information. Here is your assessment."
_USER_TEXT_JOINER = " . "


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #
async def predict_from_text(
    db: AsyncSession,
    payload: SymptomTextRequest,
    user: User | None,
) -> PredictionResponse:
    """
    Main orchestrator for `POST /api/v1/predict/text`.

    Stateful: pass ``payload.log_id`` to continue an existing chat thread,
    or omit it to start a new one. Guests (no user) are supported in both
    modes — their rows are stored with ``user_id=NULL``.
    """
    language: LanguageLiteral = _resolve_language(user)
    user_id = getattr(user, "user_id", None)

    # ---- 1. PII redaction (dual output) for THIS turn -------------------- #
    ml_text, db_text = preprocess_service.redact_pii(payload.text)
    logger.debug("PII redaction: db_len=%d ml_len=%d", len(db_text), len(ml_text))

    # ---- 2. Resolve / create the conversation row ------------------------ #
    log = await _resolve_log(
        db, log_id=payload.log_id, user_id=user_id,
    )

    # ---- 3. Append the user turn to chat_history ------------------------- #
    history = _coerce_history(log.chat_history)
    history.append({"role": "user", "text": db_text, "ml_text": ml_text})
    log.chat_history = history

    # ---- 4. Build the combined ml-text (every user turn so far) ---------- #
    combined_ml_text = _join_user_messages(history)
    cleaned_text = preprocess_service.clean_text(combined_ml_text)
    extracted_symptoms = preprocess_service.extract_symptoms(combined_ml_text)
    logger.debug(
        "NLP: cleaned=%r extracted_symptoms=%s", cleaned_text, extracted_symptoms,
    )

    # Mirror the joined patient text into raw_text so the existing sidebar /
    # admin / PDF readers see the full conversation context.
    log.raw_text = _join_user_db_text(history)

    # ---- 5. ML prediction ------------------------------------------------ #
    raw_predictions = await ml_client.predict_top_k(
        cleaned_text or combined_ml_text, k=3
    )
    top_confidence = float(raw_predictions[0]["confidence"]) if raw_predictions else 0.0
    logger.info(
        "ML: %s (stub_mode=%s top=%.3f threshold=%.3f)",
        [(p["condition"], round(p["confidence"], 3)) for p in raw_predictions],
        ml_client.is_stub(),
        top_confidence,
        settings.PREDICTION_CONFIDENCE_THRESHOLD,
    )

    # ---- 6. Threshold branch --------------------------------------------- #
    # Confidence is the SOLE gate. Stay in chat mode until the classifier
    # crosses the configured threshold — no hardcoded round cap.
    confidence_status = ConfidenceStatus(
        current_topk=top_confidence,
        required_topk=settings.PREDICTION_CONFIDENCE_THRESHOLD,
    )

    if top_confidence < settings.PREDICTION_CONFIDENCE_THRESHOLD:
        return await _ask_followup(
            db=db,
            log=log,
            history=history,
            asked_so_far=_assistant_questions(history),
            language=language,
            confidence_status=confidence_status,
        )

    return await _finalise_prediction(
        db=db,
        log=log,
        history=history,
        payload=payload,
        user_id=user_id,
        language=language,
        ml_text_for_keywords=combined_ml_text,
        cleaned_text=cleaned_text,
        extracted_symptoms=extracted_symptoms,
        raw_predictions=raw_predictions,
        confidence_status=confidence_status,
    )


# --------------------------------------------------------------------------- #
# Conversation helpers                                                         #
# --------------------------------------------------------------------------- #
async def _resolve_log(
    db: AsyncSession, *, log_id: int | None, user_id: int | None,
) -> SymptomLog:
    """
    Fetch the requested chat row (verifying ownership) or instantiate a fresh
    detached SymptomLog the caller can mutate before the first commit.

    For guests (``user_id is None``) the log must also be guest-owned —
    otherwise we 404 to avoid leaking the existence of someone else's id.
    """
    if log_id is None:
        log = SymptomLog(user_id=user_id, raw_text="", chat_history=[])
        db.add(log)
        return log

    existing = await log_repo.get_by_id(db, log_id=log_id)
    if existing is None or existing.user_id != user_id:
        raise NotFoundError(
            message="Consultation not found",
            code="chat_log_not_found",
        )
    if existing.chat_history is None:
        existing.chat_history = []
    return existing


def _coerce_history(raw: Any) -> list[dict[str, Any]]:
    """
    SQLAlchemy returns the JSON column as a Python list/dict already, but
    legacy rows can come back as None or even as a JSON string. Normalise to
    a plain list of dicts so callers can append safely.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []


def _join_user_messages(history: list[dict[str, Any]]) -> str:
    """
    Concatenate every ``user`` turn's ml_text (PII-stripped, no placeholder)
    into one string for the classifier. Falls back to display ``text`` for
    legacy rows that pre-date the ml_text field.
    """
    chunks: list[str] = []
    for turn in history:
        if turn.get("role") != "user":
            continue
        chunk = (turn.get("ml_text") or turn.get("text") or "").strip()
        if chunk:
            chunks.append(chunk)
    return _USER_TEXT_JOINER.join(chunks)


def _join_user_db_text(history: list[dict[str, Any]]) -> str:
    """
    Same concatenation, but uses the display ``text`` (with ``[REDACTED]``
    placeholders) — what we want to mirror into ``symptom_logs.raw_text`` so
    admin reviewers see the same characters the user saw.
    """
    chunks: list[str] = []
    for turn in history:
        if turn.get("role") != "user":
            continue
        chunk = (turn.get("text") or "").strip()
        if chunk:
            chunks.append(chunk)
    return _USER_TEXT_JOINER.join(chunks)


def _assistant_questions(history: list[dict[str, Any]]) -> list[str]:
    """All assistant turns asked so far (used to avoid repeating questions)."""
    return [
        str(turn.get("text") or "")
        for turn in history
        if turn.get("role") == "assistant" and turn.get("text")
    ]


def _public_history(history: list[dict[str, Any]]) -> list[ChatTurn]:
    """Strip internal fields (``ml_text``) before sending the chat to clients."""
    out: list[ChatTurn] = []
    for turn in history:
        role = turn.get("role")
        text = turn.get("text")
        if role in ("user", "assistant") and isinstance(text, str):
            out.append(ChatTurn(role=role, text=text))
    return out


# --------------------------------------------------------------------------- #
# Follow-up branch                                                             #
# --------------------------------------------------------------------------- #
async def _ask_followup(
    *,
    db: AsyncSession,
    log: SymptomLog,
    history: list[dict[str, Any]],
    asked_so_far: list[str],
    language: LanguageLiteral,
    confidence_status: ConfidenceStatus,
) -> PredictionResponse:
    """Pick a clarifying question, append it, persist, and return early."""
    question = pick_followup_question(exclude=asked_so_far)
    history.append({"role": "assistant", "text": question})
    log.chat_history = history

    saved = await log_repo.save(db, log)
    logger.info(
        "Follow-up asked: log_id=%s rounds_so_far=%d top=%.3f required=%.3f",
        saved.log_id, len(asked_so_far) + 1,
        confidence_status.current_topk, confidence_status.required_topk,
    )

    return PredictionResponse(
        log_id=saved.log_id,
        language=language,
        requires_followup=True,
        followup_message=question,
        chat_history=_public_history(history),
        confidence_status=confidence_status,
        disclaimer=get_disclaimer(language),
    )


# --------------------------------------------------------------------------- #
# Final-assessment branch                                                      #
# --------------------------------------------------------------------------- #
async def _finalise_prediction(
    *,
    db: AsyncSession,
    log: SymptomLog,
    history: list[dict[str, Any]],
    payload: SymptomTextRequest,
    user_id: int | None,
    language: LanguageLiteral,
    ml_text_for_keywords: str,
    cleaned_text: str,
    extracted_symptoms: list[str],
    raw_predictions: list[dict[str, Any]],
    confidence_status: ConfidenceStatus,
) -> PredictionResponse:
    """Run KB enrichment + triage + LIME, persist the final state, return."""
    english_names = [p["condition"] for p in raw_predictions]
    kb_index: dict[str, LocalizedDisease] = await kb_service.bulk_lookup(
        db, english_names, language=language
    )
    hits = [name for name in english_names if name.lower() in kb_index]
    logger.info(
        "KB: %d/%d hit (%s); miss=%s",
        len(hits), len(english_names),
        ", ".join(hits),
        ", ".join(n for n in english_names if n.lower() not in kb_index) or "—",
    )

    top_conditions: list[TopCondition] = [
        _build_top_condition(p["condition"], float(p["confidence"]), kb_index)
        for p in raw_predictions
    ]
    primary = top_conditions[0] if top_conditions else None
    primary_kb = kb_index.get(english_names[0].lower()) if english_names else None

    triage_level, triage_rule = _compute_triage(
        payload=payload,
        text_lower=ml_text_for_keywords.lower(),
        extracted_symptoms=extracted_symptoms,
        kb_entry=primary_kb,
    )
    logger.info(
        "Triage decision: %s (rule=%s, kb_triage=%s, severity=%s, age=%s, "
        "pregnancy=%s, chronic=%s)",
        triage_level, triage_rule,
        getattr(primary_kb, "triage_category", None),
        payload.severity, payload.age, payload.pregnancy,
        bool(payload.chronic_disease),
    )

    embedder, classifier = ml_client.get_explainer_artefacts()
    explain_payload = await get_explain_service().get_explanation(
        text=ml_text_for_keywords,
        top_predictions=raw_predictions,
        vectorizer=embedder,
        model=classifier,
        language=language,
        extracted_symptoms=extracted_symptoms,
        cleaned_text=cleaned_text,
        top_condition_display_name=primary.name if primary else "Unknown",
    )
    explanation = Explanation(
        rationale=explain_payload["rationale"],
        key_symptoms=explain_payload["key_symptoms"],
        feature_importance=[
            FeatureImportance(**fi) for fi in explain_payload["feature_importance"]
        ],
        confidence_breakdown=explain_payload["confidence_breakdown"],
    )

    # Append the closing assistant turn before persisting.
    history.append({"role": "assistant", "text": _FINAL_ASSISTANT_MESSAGE})
    log.chat_history = history

    saved = await _persist_final(
        db=db,
        log=log,
        user_id=user_id,
        payload=payload,
        top_conditions=top_conditions,
        triage_level=triage_level,
        triage_rule=triage_rule,
        extracted_symptoms=extracted_symptoms,
        language=language,
        explanation=explanation,
    )

    return PredictionResponse(
        log_id=saved.log_id,
        language=language,
        requires_followup=False,
        followup_message=None,
        chat_history=_public_history(history),
        confidence_status=confidence_status,
        top_conditions=top_conditions,
        triage_level=triage_level,
        recommended_specialist=primary.specialist_type if primary else None,
        care_tips=primary.care_tips if primary else CareTipsBilingual(),
        red_flags=primary.red_flags if primary else [],
        extracted_symptoms=list(extracted_symptoms),
        explanation=explanation,
        disclaimer=get_disclaimer(language),
    )


# --------------------------------------------------------------------------- #
# Helpers — language / KB / top-condition assembly                              #
# --------------------------------------------------------------------------- #
def _resolve_language(user: User | None) -> LanguageLiteral:
    pref = getattr(user, "language_pref", None)
    return "Urdu" if pref == "Urdu" else "English"


def _build_top_condition(
    english_name: str,
    confidence: float,
    kb_index: dict[str, LocalizedDisease],
) -> TopCondition:
    loc = kb_index.get(english_name.lower())
    if loc is None:
        return TopCondition(
            name=english_name,
            name_en=english_name,
            confidence=confidence,
        )

    return TopCondition(
        name=loc.name,
        name_en=loc.name_en,
        confidence=confidence,
        specialist_type=loc.specialist_type,
        triage_category=_normalize_triage(loc.triage_category),
        care_tips=CareTipsBilingual(
            en=loc.care_tips_en,
            ur=loc.care_tips_ur,
        ),
        red_flags=_split_red_flags(loc.red_flags),
    )


# --------------------------------------------------------------------------- #
# Smart triage engine (UC-03)                                                  #
# --------------------------------------------------------------------------- #
def _compute_triage(
    payload: SymptomTextRequest,
    text_lower: str,
    extracted_symptoms: list[str],
    kb_entry: LocalizedDisease | None,
) -> tuple[TriageLevel, str]:
    extracted_set = {s.lower() for s in extracted_symptoms}

    fired = _first_match(text_lower, extracted_set, _CRITICAL_KEYWORDS)
    if fired:
        return "urgent_care", f"critical_keyword:{fired}"

    if payload.pregnancy and (
        _first_match(text_lower, extracted_set, _PREGNANCY_ESCALATION_KEYWORDS)
        or payload.severity == "severe"
    ):
        return "urgent_care", "pregnancy_escalation"

    severe_or_moderate = payload.severity in {"moderate", "severe"}

    if payload.age is not None and (
        payload.age < _AGE_VULNERABLE_LOWER or payload.age > _AGE_VULNERABLE_UPPER
    ) and severe_or_moderate:
        return "urgent_care", f"vulnerable_age:{payload.age}"

    if payload.chronic_disease and payload.severity == "severe":
        return "urgent_care", "chronic_disease_severe"

    kb_triage = _normalize_triage(
        kb_entry.triage_category if kb_entry else None
    )
    if kb_triage == "urgent_care":
        return "urgent_care", "kb_urgent_care"

    if payload.severity == "severe":
        return "urgent_care", "severity_severe"

    if kb_triage == "see_gp":
        return "see_gp", "kb_see_gp"

    if payload.severity == "moderate":
        return "see_gp", "severity_moderate"

    if kb_triage == "self_care":
        return "self_care", "kb_self_care"

    return "self_care", "default"


def _first_match(
    text: str, extracted: set[str], keywords: Iterable[str]
) -> str | None:
    for kw in keywords:
        if kw in text or kw in extracted:
            return kw
    return None


def _normalize_triage(value: str | None) -> TriageLevel | None:
    if not value:
        return None
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    mapping: dict[str, TriageLevel] = {
        "urgent_care": "urgent_care",
        "urgent": "urgent_care",
        "emergency": "urgent_care",
        "er": "urgent_care",
        "see_gp": "see_gp",
        "moderate": "see_gp",
        "self_care": "self_care",
        "selfcare": "self_care",
        "mild": "self_care",
    }
    return mapping.get(v)


# --------------------------------------------------------------------------- #
# Red-flag parsing                                                             #
# --------------------------------------------------------------------------- #
def _split_red_flags(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts: list[str] = []
    text = raw.replace("|", "\n").replace(";", "\n").replace(",", "\n")
    for chunk in text.splitlines():
        item = chunk.strip(" -*\t")
        if item:
            parts.append(item)
    return parts


# --------------------------------------------------------------------------- #
# Persistence — final assessment                                               #
# --------------------------------------------------------------------------- #
async def _persist_final(
    *,
    db: AsyncSession,
    log: SymptomLog,
    user_id: int | None,
    payload: SymptomTextRequest,
    top_conditions: list[TopCondition],
    triage_level: TriageLevel,
    triage_rule: str,
    extracted_symptoms: list[str],
    language: LanguageLiteral,
    explanation: Explanation,
) -> SymptomLog:
    """
    Write the final prediction bundle onto the existing chat row.

    The English class name lives in ``predicted_condition`` so analytics
    aren't fragmented by language; the full bilingual + explainability
    bundle is JSON-encoded into ``explanation_json``.
    """
    explanation_blob = {
        "language": language,
        "triage_rule": triage_rule,
        "stub_mode": ml_client.is_stub(),
        "top_conditions": [
            cast(dict, c.model_dump()) for c in top_conditions
        ],
        "extracted_symptoms": extracted_symptoms,
        "explanation": explanation.model_dump(),
        "metadata": {
            "age": payload.age,
            "sex": payload.sex,
            "duration": payload.duration,
            "severity": payload.severity,
            "pregnancy": payload.pregnancy,
            "chronic_disease": payload.chronic_disease,
        },
    }

    primary = top_conditions[0] if top_conditions else None
    log.user_id = user_id
    log.predicted_condition = primary.name_en if primary else None
    log.confidence_score = primary.confidence if primary else None
    log.explanation_json = json.dumps(explanation_blob, ensure_ascii=False)
    log.triage_level = triage_level
    return await log_repo.save(db, log)
