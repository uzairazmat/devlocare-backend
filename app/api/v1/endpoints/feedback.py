"""
UC-08 — Consultation feedback endpoints.

Two routes, mounted under ``/api/v1/feedback``:

* ``POST /``         — submit (or idempotently update) feedback for the
  caller's own consultation log.
* ``GET  /monitor``  — lightweight aggregate stats for admin / model
  monitoring usage.

Both endpoints require a valid JWT. The monitoring endpoint is currently
gated by plain authentication only — the project does not yet have a role
system; the TODO in :func:`monitor_feedback` documents exactly where to
add a staff/admin guard once roles land.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.request import FeedbackCreateRequest
from app.models.response import (
    FeedbackMonitorResponse,
    FeedbackResponse,
    FeedbackSubmitResponse,
)
from app.repositories import feedback_repo, log_repo

logger = get_logger(__name__)
router = APIRouter(tags=["Feedback"])


# --------------------------------------------------------------------------- #
# POST /feedback — submit / update feedback                                    #
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=FeedbackSubmitResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit (or update) feedback for one of your consultations",
    description=(
        "Records a 1-5 helpfulness rating and an optional free-text "
        "comment against an existing consultation log owned by the "
        "authenticated user. If feedback already exists for the same "
        "(log_id, user_id) pair, the row is updated in place — duplicate "
        "spam is impossible thanks to a unique constraint on the table."
    ),
)
async def submit_feedback(
    payload: FeedbackCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackSubmitResponse:
    # Ownership guard — fetch the target log and ensure it belongs to the
    # caller. We reply 404 (not 403) when the log isn't theirs to avoid
    # leaking the existence of other users' log_ids — same convention used
    # by the history delete endpoint.
    target_log = await log_repo.get_by_id(db, log_id=payload.log_id)
    if target_log is None or target_log.user_id != current_user.user_id:
        logger.warning(
            "Feedback POST: rejected user_id=%s log_id=%s (not found / not owned)",
            current_user.user_id, payload.log_id,
        )
        raise NotFoundError(
            message="Consultation not found",
            code="feedback_log_not_found",
        )

    row, created = await feedback_repo.create_or_update_feedback(
        db,
        log_id=payload.log_id,
        user_id=current_user.user_id,
        helpfulness_rating=payload.helpfulness_rating,
        feedback_text=payload.feedback_text,
    )

    logger.info(
        "AUDIT feedback.%s user_id=%s log_id=%s feedback_id=%s rating=%s "
        "has_text=%s at=%s",
        "create" if created else "update",
        current_user.user_id,
        row.log_id,
        row.feedback_id,
        row.helpfulness_rating,
        bool(row.feedback_text),
        datetime.now(timezone.utc).isoformat(),
    )

    message = (
        "Feedback submitted successfully"
        if created
        else "Feedback updated successfully"
    )
    return FeedbackSubmitResponse(
        message=message,
        feedback=FeedbackResponse.model_validate(row),
    )


# --------------------------------------------------------------------------- #
# GET /feedback/monitor — aggregate analytics                                  #
# --------------------------------------------------------------------------- #
@router.get(
    "/monitor",
    response_model=FeedbackMonitorResponse,
    summary="Aggregated feedback statistics for model-quality monitoring",
    description=(
        "Returns headline counters, the rating distribution, recent "
        "low-rated consultations, the most recent negative comments, and "
        "breakdowns by triage level and predicted condition. Intended as "
        "a single lightweight endpoint a future admin dashboard or "
        "scheduled QA report can poll."
    ),
)
async def monitor_feedback(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackMonitorResponse:
    # NOTE: This endpoint is currently protected by standard authentication
    # only. The project does not yet have an admin/staff role system; once
    # one exists, replace `get_current_user` above with a `require_admin`
    # dependency (or add a role check here) so non-admins are rejected
    # with 403. Until then we audit every access so abuse is traceable.
    logger.info(
        "AUDIT feedback.monitor user_id=%s username=%s at=%s",
        current_user.user_id,
        current_user.username,
        datetime.now(timezone.utc).isoformat(),
    )

    stats = await feedback_repo.get_feedback_monitoring_stats(db)
    return FeedbackMonitorResponse.model_validate(stats)
