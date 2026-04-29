"""
Admin-dashboard repository — every aggregate query the admin landing page
needs, kept in one place.

Design notes
------------
* All queries are SQL aggregates (``COUNT`` / ``AVG`` / ``GROUP BY``); no
  Python-side scans. The dashboard stays cheap as ``symptom_logs`` grows.
* Language usage is computed by left-joining ``symptom_logs`` with
  ``users`` and falling back to ``"English"`` for guest rows
  (``user_id IS NULL``) — that mirrors the rule used inside
  ``prediction_service`` when persisting a log.
* The detailed JSON blob inside ``explanation_json`` is intentionally NOT
  parsed at the SQL layer. The dashboard works off the dedicated columns
  (``predicted_condition``, ``triage_level``, ``confidence_score``).
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConsultationFeedback, SymptomLog, User


URGENT_TRIAGE: str = "urgent_care"

# Canonical triage values currently emitted by the prediction pipeline.
# Anything outside this set (legacy / dirty rows like "moderate") is
# bucketed as "unknown" so the dashboard stays clean.
_CANONICAL_TRIAGE: frozenset[str] = frozenset({"self_care", "see_gp", "urgent_care"})


# --------------------------------------------------------------------------- #
# Headline counters                                                            #
# --------------------------------------------------------------------------- #
async def get_headline_counters(db: AsyncSession) -> dict[str, Any]:
    """
    Return ``{total, today, urgent, avg_rating, total_feedback}`` in two
    cheap round-trips (one for symptom_logs, one for feedback).
    """
    today_start = datetime.combine(
        datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc,
    )

    log_stmt = select(
        func.count(SymptomLog.log_id).label("total"),
        func.coalesce(
            func.sum(case((SymptomLog.created_at >= today_start, 1), else_=0)),
            0,
        ).label("today"),
        func.coalesce(
            func.sum(case((SymptomLog.triage_level == URGENT_TRIAGE, 1), else_=0)),
            0,
        ).label("urgent"),
    )
    log_row = (await db.execute(log_stmt)).one()

    fb_stmt = select(
        func.count(ConsultationFeedback.feedback_id).label("total"),
        func.coalesce(
            func.avg(ConsultationFeedback.helpfulness_rating), 0.0
        ).label("avg"),
    )
    fb_row = (await db.execute(fb_stmt)).one()

    return {
        "total_consultations": int(log_row.total or 0),
        "today_consultations": int(log_row.today or 0),
        "urgent_cases": int(log_row.urgent or 0),
        "average_feedback_rating": round(float(fb_row.avg or 0.0), 2),
        "total_feedback": int(fb_row.total or 0),
    }


# --------------------------------------------------------------------------- #
# Most predicted diseases                                                      #
# --------------------------------------------------------------------------- #
async def get_top_diseases(
    db: AsyncSession, *, limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Return the ``limit`` most-predicted main conditions with their average
    confidence. ``predicted_condition`` is always the canonical English
    name so the same row is never split across languages.
    """
    stmt = (
        select(
            SymptomLog.predicted_condition,
            func.count(SymptomLog.log_id).label("count"),
            func.coalesce(func.avg(SymptomLog.confidence_score), 0.0).label("avg"),
        )
        .where(SymptomLog.predicted_condition.is_not(None))
        .group_by(SymptomLog.predicted_condition)
        .order_by(func.count(SymptomLog.log_id).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "main_condition": r.predicted_condition,
            "count": int(r.count),
            "avg_confidence": round(float(r.avg or 0.0), 4),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Triage distribution                                                          #
# --------------------------------------------------------------------------- #
async def get_triage_distribution(db: AsyncSession) -> list[dict[str, Any]]:
    """
    Return one row per triage level with absolute counts AND a percentage
    of the total (rounded to 2 dp). NULL triage levels are bucketed as
    ``"unknown"`` rather than dropped so the donut chart always sums to 100.
    """
    stmt = (
        select(
            SymptomLog.triage_level,
            func.count(SymptomLog.log_id).label("count"),
        )
        .group_by(SymptomLog.triage_level)
        .order_by(func.count(SymptomLog.log_id).desc())
    )
    rows = (await db.execute(stmt)).all()
    total = sum(int(r.count) for r in rows) or 1

    # Bucket non-canonical (legacy / dirty) triage values into a single
    # "unknown" slice so the donut chart only shows the four expected labels.
    bucketed: dict[str, int] = {}
    for r in rows:
        label = r.triage_level if r.triage_level in _CANONICAL_TRIAGE else "unknown"
        bucketed[label] = bucketed.get(label, 0) + int(r.count)

    return [
        {
            "triage_level": label,
            "count": count,
            "percentage": round(count / total * 100.0, 2),
        }
        for label, count in sorted(bucketed.items(), key=lambda kv: kv[1], reverse=True)
    ]


# --------------------------------------------------------------------------- #
# Language usage                                                               #
# --------------------------------------------------------------------------- #
async def get_language_usage(db: AsyncSession) -> list[dict[str, Any]]:
    """
    Return one row per language with absolute counts AND a percentage.

    Logs with ``user_id IS NULL`` are guest calls — :mod:`prediction_service`
    forces them to English, so we apply the same rule here via
    ``COALESCE(language_pref, 'English')``.
    """
    language_expr = func.coalesce(User.language_pref, "English").label("language")

    stmt = (
        select(language_expr, func.count(SymptomLog.log_id).label("count"))
        .select_from(SymptomLog)
        .join(User, User.user_id == SymptomLog.user_id, isouter=True)
        .group_by(language_expr)
        .order_by(func.count(SymptomLog.log_id).desc())
    )
    rows = (await db.execute(stmt)).all()
    total = sum(int(r.count) for r in rows) or 1

    return [
        {
            "language": r.language or "Unknown",
            "count": int(r.count),
            "percentage": round(int(r.count) / total * 100.0, 2),
        }
        for r in rows
    ]
