"""
Admin-dashboard service layer.

Thin orchestrator: each method maps to one chart on the admin landing
screen. The ``build_full_dashboard`` helper bundles them all into the
single payload the frontend expects from ``GET /admin/dashboard``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import (
    DashboardCounters,
    DashboardResponse,
    LanguageUsageStat,
    TopDiseaseStat,
    TriageDistributionStat,
)
from app.repositories.admin import dashboard_repo


# --------------------------------------------------------------------------- #
# Per-section helpers                                                          #
# --------------------------------------------------------------------------- #
async def get_counters(db: AsyncSession) -> DashboardCounters:
    return DashboardCounters.model_validate(
        await dashboard_repo.get_headline_counters(db)
    )


async def get_top_diseases(
    db: AsyncSession, *, limit: int = 10,
) -> list[TopDiseaseStat]:
    rows = await dashboard_repo.get_top_diseases(db, limit=limit)
    return [TopDiseaseStat.model_validate(r) for r in rows]


async def get_triage_distribution(
    db: AsyncSession,
) -> list[TriageDistributionStat]:
    rows = await dashboard_repo.get_triage_distribution(db)
    return [TriageDistributionStat.model_validate(r) for r in rows]


async def get_language_usage(db: AsyncSession) -> list[LanguageUsageStat]:
    rows = await dashboard_repo.get_language_usage(db)
    return [LanguageUsageStat.model_validate(r) for r in rows]


# --------------------------------------------------------------------------- #
# Combined "one HTTP call" payload                                             #
# --------------------------------------------------------------------------- #
async def build_full_dashboard(
    db: AsyncSession, *, top_diseases_limit: int = 10,
) -> DashboardResponse:
    """
    Assemble the complete dashboard payload. Each section is computed by
    its own dedicated SQL aggregate — there is no Python-side joining.
    """
    counters = await get_counters(db)
    top_diseases = await get_top_diseases(db, limit=top_diseases_limit)
    triage = await get_triage_distribution(db)
    languages = await get_language_usage(db)

    return DashboardResponse(
        counters=counters,
        top_diseases=top_diseases,
        triage_distribution=triage,
        language_usage=languages,
        generated_at=datetime.now(timezone.utc),
    )
