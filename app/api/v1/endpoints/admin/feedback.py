"""
Admin feedback endpoints — mounted under ``/api/v1/admin/feedback``.

* ``GET /summary`` — total, average rating, % positive feedback,
                     rating distribution.
* ``GET /``        — paginated recent feedback with rating, comment, and
                     parent consultation reference.

The deeper "model-quality monitoring" payload (low-rated cases, breakdown
by triage / condition) is still served by the original
``GET /api/v1/feedback/monitor`` endpoint and is intentionally unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.admin import (
    FeedbackAdminListResponse,
    FeedbackAdminSummary,
)
from app.services.admin import feedback_admin_service

logger = get_logger(__name__)
router = APIRouter(tags=["Admin · Feedback"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


@router.get(
    "/summary",
    response_model=FeedbackAdminSummary,
    summary="Feedback summary cards (total, average, % positive)",
)
async def get_feedback_summary(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> FeedbackAdminSummary:
    return await feedback_admin_service.get_summary(db)


@router.get(
    "",
    response_model=FeedbackAdminListResponse,
    summary="Recent feedback entries (paginated)",
)
async def list_recent_feedback(
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> FeedbackAdminListResponse:
    return await feedback_admin_service.list_recent(
        db, limit=limit, offset=offset,
    )
