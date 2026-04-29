"""
UC-06 — Consultation history & PDF export.

Three endpoints, all mounted under ``/api/v1/history``:

* ``GET    /``                — paginated sidebar list of saved consultations
* ``DELETE /{log_id}``        — permanently remove one consultation (owner only)
* ``POST   /export``          — download a PDF medical report for selected logs

All endpoints are protected: only the JWT-bearing owner of a record can read,
delete or export it. The GET endpoint is the only one that allows
unauthenticated callers — they get an empty envelope plus a friendly hint to
log in. (DELETE / EXPORT remain auth-required because a guest has no logs of
their own to act on.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user, get_current_user_optional
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import SymptomLog, User
from app.db.session import get_db
from app.models.request import HistoryExportRequest
from app.models.response import (
    HistoryItem,
    HistoryResponse,
    MessageResponse,
    TriageLevel,
)
from app.repositories import log_repo
from app.services import pdf_service

logger = get_logger(__name__)
router = APIRouter(tags=["History"])


_GUEST_MESSAGE = "Login to save and view your consultation history"
_PREVIEW_MAX_CHARS = 140
_DEFAULT_LIMIT = 15
_MAX_LIMIT = 50


# --------------------------------------------------------------------------- #
# GET /history — paginated sidebar list                                        #
# --------------------------------------------------------------------------- #
@router.get(
    "",
    response_model=HistoryResponse,
    summary="List saved consultations for the current user (paginated)",
    description=(
        "Returns the authenticated user's saved consultations, newest first, "
        "for the ChatGPT-style sidebar. Heavy fields (full predictions, "
        "feature importance) are omitted — use POST /history/export to get "
        "the complete data as a PDF. Guests receive an empty envelope with a "
        "friendly login prompt."
    ),
)
async def list_history(
    limit: int = Query(
        default=_DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description="Page size (1-50). Defaults to 15.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of records to skip for pagination.",
    ),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    if current_user is None:
        logger.info(
            "History GET: guest call rejected (limit=%s offset=%s)",
            limit, offset,
        )
        return HistoryResponse(
            items=[],
            total=0,
            has_more=False,
            limit=limit,
            offset=offset,
            message=_GUEST_MESSAGE,
        )

    items, total = await log_repo.get_user_history(
        db, user_id=current_user.user_id, limit=limit, offset=offset,
    )

    history_items = [_to_history_item(row) for row in items]
    has_more = (offset + len(history_items)) < total

    logger.info(
        "History GET: user_id=%s returned=%d total=%d has_more=%s "
        "limit=%d offset=%d",
        current_user.user_id, len(history_items), total, has_more,
        limit, offset,
    )
    return HistoryResponse(
        items=history_items,
        total=total,
        has_more=has_more,
        limit=limit,
        offset=offset,
        message=None,
    )


# --------------------------------------------------------------------------- #
# DELETE /history/{log_id} — single delete with ownership guard                #
# --------------------------------------------------------------------------- #
@router.delete(
    "/{log_id}",
    response_model=MessageResponse,
    summary="Delete a single consultation from history (owner only)",
    description=(
        "Permanently deletes the specified consultation. The record can only "
        "be removed by its owner; for any other caller the endpoint returns "
        "404 (we deliberately do not distinguish 'not found' from 'not "
        "yours' to avoid leaking the existence of foreign log_ids)."
    ),
)
async def delete_history_item(
    log_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    deleted = await log_repo.delete_log(
        db, log_id=log_id, user_id=current_user.user_id,
    )
    if deleted == 0:
        logger.warning(
            "History DELETE: log_id=%s NOT removed for user_id=%s "
            "(not found or not owned)",
            log_id, current_user.user_id,
        )
        raise NotFoundError(
            message="Consultation not found",
            code="history_not_found",
        )

    logger.info(
        "AUDIT history.delete user_id=%s log_id=%s deleted=%d at=%s",
        current_user.user_id, log_id, deleted,
        datetime.now(timezone.utc).isoformat(),
    )
    return MessageResponse(
        message=f"Consultation {log_id} deleted successfully",
    )


# --------------------------------------------------------------------------- #
# POST /history/export — downloadable PDF                                      #
# --------------------------------------------------------------------------- #
@router.post(
    "/export",
    summary="Export consultation history as a downloadable PDF",
    description=(
        "Generates a professional PDF medical consultation report from the "
        "user's stored ``symptom_logs`` data. Supply either ``log_ids`` "
        "(explicit selection) or ``last_n`` (most recent N). The response is "
        "an ``application/pdf`` octet stream the browser can save directly."
    ),
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "PDF report stream",
        },
    },
)
async def export_history(
    payload: HistoryExportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        mode = payload.resolved_mode()
    except ValueError as exc:
        raise ValidationError(message=str(exc), code="invalid_export_request") from exc

    if mode == "log_ids":
        logs = await log_repo.get_logs_for_export(
            db, user_id=current_user.user_id, log_ids=payload.log_ids,
        )
    else:
        logs = await log_repo.get_logs_for_export(
            db, user_id=current_user.user_id, last_n=payload.last_n,
        )

    if not logs:
        logger.info(
            "History EXPORT: user_id=%s mode=%s produced 0 rows",
            current_user.user_id, mode,
        )
        raise NotFoundError(
            message="No consultations available for export",
            code="history_export_empty",
        )

    pdf_bytes = pdf_service.build_history_pdf(
        logs, username=current_user.username,
    )

    filename = _build_filename(current_user.username)
    logger.info(
        "AUDIT history.export user_id=%s mode=%s rows=%d size=%dB filename=%s",
        current_user.user_id, mode, len(logs), len(pdf_bytes), filename,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        status_code=status.HTTP_200_OK,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
            "Cache-Control": "no-store",
            "X-Export-Rows": str(len(logs)),
        },
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _to_history_item(row: SymptomLog) -> HistoryItem:
    """Project a DB row into the lightweight sidebar shape."""
    triage = cast(TriageLevel | None, row.triage_level) if row.triage_level else None
    return HistoryItem(
        log_id=row.log_id,
        created_at=row.created_at,
        raw_text=_preview(row.raw_text),
        main_condition=row.predicted_condition,
        triage_level=triage,
    )


def _preview(text: str | None) -> str:
    """Trim free-form symptom text to a single-line sidebar preview."""
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= _PREVIEW_MAX_CHARS:
        return cleaned
    return cleaned[: _PREVIEW_MAX_CHARS - 1].rstrip() + "\u2026"


def _build_filename(username: str | None) -> str:
    """Produce a friendly, filesystem-safe download filename."""
    safe_user = "user"
    if username:
        safe_user = "".join(
            c if c.isalnum() or c in {"_", "-"} else "_" for c in username
        ).strip("_") or "user"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"devlocare-consultations-{safe_user}-{stamp}.pdf"
