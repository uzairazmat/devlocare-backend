"""
Admin feedback service — thin orchestrator over
:mod:`app.repositories.admin.feedback_admin_repo`.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import (
    FeedbackAdminListItem,
    FeedbackAdminListResponse,
    FeedbackAdminSummary,
)
from app.repositories.admin import feedback_admin_repo


async def get_summary(db: AsyncSession) -> FeedbackAdminSummary:
    payload = await feedback_admin_repo.get_admin_summary(db)
    return FeedbackAdminSummary.model_validate(payload)


async def list_recent(
    db: AsyncSession, *, limit: int, offset: int,
) -> FeedbackAdminListResponse:
    rows, total = await feedback_admin_repo.list_recent_feedback(
        db, limit=limit, offset=offset,
    )

    items = [
        FeedbackAdminListItem(
            feedback_id=int(r.feedback_id),
            log_id=int(r.log_id),
            user_id=int(r.user_id),
            username=r.username,
            helpfulness_rating=int(r.helpfulness_rating),
            feedback_text=r.feedback_text,
            main_condition=r.main_condition,
            triage_level=r.triage_level,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return FeedbackAdminListResponse(
        items=items,
        total=total,
        has_more=(offset + len(items)) < total,
        limit=limit,
        offset=offset,
    )
