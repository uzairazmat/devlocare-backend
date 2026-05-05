"""
Admin-side feedback queries.

We deliberately keep the heavy ``feedback_monitor`` aggregator in
:mod:`app.repositories.feedback_repo` (already powering the legacy
``GET /api/v1/feedback/monitor`` endpoint). This module owns the two
extra queries the new admin panel needs:

* :func:`get_admin_summary`     — total / avg / % positive / distribution.
* :func:`list_recent_feedback`  — paginated recent feedback joined to
                                  the parent log so each row carries its
                                  ``main_condition`` and ``triage_level``.
"""
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConsultationFeedback, SymptomLog, User


POSITIVE_THRESHOLD: int = 4   # rating >= 4 → "positive"
NEGATIVE_THRESHOLD: int = 2   # rating <= 2 → "negative"


# --------------------------------------------------------------------------- #
# Summary                                                                      #
# --------------------------------------------------------------------------- #
async def get_admin_summary(db: AsyncSession) -> dict[str, Any]:
    """Headline counters + rating distribution in two cheap queries."""
    F = ConsultationFeedback

    headline_stmt = select(
        func.count(F.feedback_id).label("total"),
        func.coalesce(func.avg(F.helpfulness_rating), 0.0).label("avg"),
        func.coalesce(
            func.sum(case((F.helpfulness_rating >= POSITIVE_THRESHOLD, 1), else_=0)),
            0,
        ).label("positive"),
        func.coalesce(
            func.sum(case((F.helpfulness_rating <= NEGATIVE_THRESHOLD, 1), else_=0)),
            0,
        ).label("negative"),
    )
    headline = (await db.execute(headline_stmt)).one()
    total = int(headline.total or 0)
    average = round(float(headline.avg or 0.0), 2)
    positive = int(headline.positive or 0)
    negative = int(headline.negative or 0)

    pct_pos = round(positive / total * 100.0, 2) if total else 0.0
    pct_neg = round(negative / total * 100.0, 2) if total else 0.0

    dist_stmt = (
        select(F.helpfulness_rating, func.count(F.feedback_id))
        .group_by(F.helpfulness_rating)
    )
    dist_rows = (await db.execute(dist_stmt)).all()
    distribution: dict[str, int] = {str(i): 0 for i in range(1, 6)}
    for rating, cnt in dist_rows:
        if rating is not None and 1 <= int(rating) <= 5:
            distribution[str(int(rating))] = int(cnt)

    return {
        "total_feedback": total,
        "average_rating": average,
        "positive_percentage": pct_pos,
        "negative_percentage": pct_neg,
        "rating_distribution": distribution,
    }


# --------------------------------------------------------------------------- #
# Recent list                                                                  #
# --------------------------------------------------------------------------- #
async def list_recent_feedback(
    db: AsyncSession, *, limit: int, offset: int,
) -> tuple[Sequence[Any], int]:
    """
    Return ``(rows, total)`` of feedback joined to its parent log + user
    so the admin table can render condition / triage / username inline.
    """
    F = ConsultationFeedback
    L = SymptomLog
    U = User

    items_stmt = (
        select(
            F.feedback_id,
            F.log_id,
            F.user_id,
            F.helpfulness_rating,
            F.feedback_text,
            F.created_at,
            L.predicted_condition.label("main_condition"),
            L.triage_level,
            U.username,
        )
        .select_from(F)
        .join(L, L.log_id == F.log_id, isouter=True)
        .join(U, U.user_id == F.user_id, isouter=True)
        .order_by(F.created_at.desc(), F.feedback_id.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count(F.feedback_id))

    rows = (await db.execute(items_stmt)).all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return rows, total
