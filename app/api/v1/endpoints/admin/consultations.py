"""
Admin consultation-monitor endpoints — mounted under
``/api/v1/admin/consultations``.

* ``GET /``            — paginated list with disease/triage/language filters
* ``GET /{log_id}``    — full detail (top-3 predictions, SHAP summary,
                        triage decision, recommended specialist, attached
                        feedback)
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.admin import (
    AdminConsultationDetail,
    AdminConsultationListResponse,
)
from app.services.admin import consultation_service

logger = get_logger(__name__)
router = APIRouter(tags=["Admin · Consultations"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

TriageFilter = Literal["self_care", "see_gp", "urgent_care"]
LanguageFilter = Literal["English", "Urdu"]


@router.get(
    "",
    response_model=AdminConsultationListResponse,
    summary="List consultations (paginated, filterable)",
)
async def list_consultations(
    disease: str | None = Query(
        default=None,
        max_length=100,
        description="Case-insensitive substring match on the predicted condition.",
    ),
    triage: TriageFilter | None = Query(default=None),
    language: LanguageFilter | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminConsultationListResponse:
    return await consultation_service.list_consultations(
        db,
        disease=disease,
        triage=triage,
        language=language,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{log_id}",
    response_model=AdminConsultationDetail,
    summary="Single consultation detail (predictions, SHAP, triage)",
)
async def get_consultation_detail(
    log_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminConsultationDetail:
    return await consultation_service.get_consultation_detail(db, log_id=log_id)
