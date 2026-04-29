"""
Admin dashboard endpoints — mounted under ``/api/v1/admin/dashboard``.

Five public routes, all admin-only:

* ``GET /``                    — combined payload for the whole screen
* ``GET /counters``            — headline KPI cards only
* ``GET /top-diseases``        — most predicted diseases bar chart
* ``GET /triage-distribution`` — triage donut chart
* ``GET /language-usage``      — language donut chart

The combined endpoint exists so a fresh page load is a single round-trip.
The per-section endpoints exist so individual widgets can be polled
without re-running the rest of the aggregates.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.admin import (
    DashboardCounters,
    DashboardResponse,
    LanguageUsageStat,
    TopDiseaseStat,
    TriageDistributionStat,
)
from app.services.admin import dashboard_service

logger = get_logger(__name__)
router = APIRouter(tags=["Admin · Dashboard"])


@router.get(
    "",
    response_model=DashboardResponse,
    summary="Full admin dashboard payload (single HTTP call)",
)
async def get_dashboard(
    top_diseases_limit: int = Query(default=10, ge=1, le=25),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    return await dashboard_service.build_full_dashboard(
        db, top_diseases_limit=top_diseases_limit,
    )


@router.get(
    "/counters",
    response_model=DashboardCounters,
    summary="Headline counters only (total, today, urgent, avg rating)",
)
async def get_counters(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DashboardCounters:
    return await dashboard_service.get_counters(db)


@router.get(
    "/top-diseases",
    response_model=list[TopDiseaseStat],
    summary="Most predicted diseases (aggregated)",
)
async def get_top_diseases(
    limit: int = Query(default=10, ge=1, le=25),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TopDiseaseStat]:
    return await dashboard_service.get_top_diseases(db, limit=limit)


@router.get(
    "/triage-distribution",
    response_model=list[TriageDistributionStat],
    summary="Triage level distribution",
)
async def get_triage_distribution(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TriageDistributionStat]:
    return await dashboard_service.get_triage_distribution(db)


@router.get(
    "/language-usage",
    response_model=list[LanguageUsageStat],
    summary="Language usage statistics",
)
async def get_language_usage(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[LanguageUsageStat]:
    return await dashboard_service.get_language_usage(db)
