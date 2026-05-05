"""
Consultation-feedback repository — UC-08.

Owns every read/write against the ``consultation_feedback`` table:

* :func:`get_by_log_and_user`     — point lookup used to decide insert vs update.
* :func:`create_or_update_feedback` — idempotent upsert respecting the
  unique ``(log_id, user_id)`` constraint.
* :func:`get_feedback_monitoring_stats` — single-call aggregate query bundle
  powering the lightweight admin/model-monitoring endpoint.

All ownership checks happen one layer up (in the endpoint), but read queries
that surface log details (``recent_low_rated_cases``,
``feedback_by_main_condition`` …) are scoped at the SQL layer to never expose
raw user-identifying information to the response.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConsultationFeedback, SymptomLog


# Anything <= this rating counts as a "low rating" complaint signal.
LOW_RATING_THRESHOLD: int = 2


# --------------------------------------------------------------------------- #
# Point reads                                                                  #
# --------------------------------------------------------------------------- #
async def get_by_log_and_user(
    db: AsyncSession,
    *,
    log_id: int,
    user_id: int,
) -> ConsultationFeedback | None:
    """
    Return the feedback row a user has previously left on ``log_id``,
    or ``None`` if they haven't.
    """
    stmt = select(ConsultationFeedback).where(
        ConsultationFeedback.log_id == log_id,
        ConsultationFeedback.user_id == user_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Upsert                                                                       #
# --------------------------------------------------------------------------- #
async def create_or_update_feedback(
    db: AsyncSession,
    *,
    log_id: int,
    user_id: int,
    helpfulness_rating: int,
    feedback_text: str | None,
) -> tuple[ConsultationFeedback, bool]:
    """
    Insert a new feedback row, or update the existing one for the same
    ``(log_id, user_id)`` pair. Honours the table's unique constraint.

    Returns
    -------
    tuple
        ``(feedback_row, created)`` — ``created`` is ``True`` for fresh
        inserts and ``False`` when an existing row was updated. The caller
        uses this purely for logging / audit purposes.
    """
    existing = await get_by_log_and_user(db, log_id=log_id, user_id=user_id)

    if existing is not None:
        existing.helpfulness_rating = helpfulness_rating
        existing.feedback_text = feedback_text
        await db.commit()
        await db.refresh(existing)
        return existing, False

    row = ConsultationFeedback(
        log_id=log_id,
        user_id=user_id,
        helpfulness_rating=helpfulness_rating,
        feedback_text=feedback_text,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, True


# --------------------------------------------------------------------------- #
# Monitoring aggregates                                                        #
# --------------------------------------------------------------------------- #
async def get_feedback_monitoring_stats(
    db: AsyncSession,
    *,
    recent_limit: int = 10,
    top_comments_limit: int = 10,
) -> dict[str, Any]:
    """
    Build the aggregated payload used by the ``GET /feedback/monitor``
    admin endpoint.

    Everything is computed via SQL aggregation — no Python-side scans of
    the feedback table — so the endpoint stays cheap as the table grows.

    The returned dict contains:

    * ``total_feedback`` (int)
    * ``average_rating`` (float, rounded to 2 dp; ``0.0`` if no rows)
    * ``rating_distribution`` (``{1..5: int}`` — every bucket is always
      present, defaulting to 0)
    * ``low_rating_count`` (rows where ``rating <= LOW_RATING_THRESHOLD``)
    * ``recent_low_rated_cases`` (list of small dicts, newest first)
    * ``top_negative_comments`` (list of small dicts, newest first)
    * ``feedback_by_triage`` (list of ``{triage_level, count, avg_rating}``)
    * ``feedback_by_main_condition`` (list of
      ``{main_condition, count, avg_rating}``)
    """
    F = ConsultationFeedback
    L = SymptomLog
    low = LOW_RATING_THRESHOLD

    # ----- headline counters -------------------------------------------------
    headline_stmt = select(
        func.count(F.feedback_id).label("total"),
        func.coalesce(func.avg(F.helpfulness_rating), 0.0).label("avg"),
        func.coalesce(
            func.sum(case((F.helpfulness_rating <= low, 1), else_=0)), 0
        ).label("low"),
    )
    headline = (await db.execute(headline_stmt)).one()
    total = int(headline.total or 0)
    average_rating = round(float(headline.avg or 0.0), 2)
    low_rating_count = int(headline.low or 0)

    # ----- rating distribution ----------------------------------------------
    dist_stmt = (
        select(F.helpfulness_rating, func.count(F.feedback_id))
        .group_by(F.helpfulness_rating)
    )
    dist_rows = (await db.execute(dist_stmt)).all()
    rating_distribution: dict[str, int] = {str(i): 0 for i in range(1, 6)}
    for rating, cnt in dist_rows:
        if rating is not None and 1 <= int(rating) <= 5:
            rating_distribution[str(int(rating))] = int(cnt)

    # ----- recent low-rated consultations -----------------------------------
    recent_stmt = (
        select(
            F.feedback_id,
            F.log_id,
            F.helpfulness_rating,
            F.feedback_text,
            F.created_at,
            L.predicted_condition,
            L.triage_level,
        )
        .join(L, L.log_id == F.log_id)
        .where(F.helpfulness_rating <= low)
        .order_by(F.created_at.desc(), F.feedback_id.desc())
        .limit(recent_limit)
    )
    recent_rows = (await db.execute(recent_stmt)).all()
    recent_low_rated_cases = [
        {
            "feedback_id": int(r.feedback_id),
            "log_id": int(r.log_id),
            "helpfulness_rating": int(r.helpfulness_rating),
            "feedback_text": r.feedback_text,
            "main_condition": r.predicted_condition,
            "triage_level": r.triage_level,
            "created_at": r.created_at,
        }
        for r in recent_rows
    ]

    # ----- most recent low-rated comments with actual text ------------------
    comments_stmt = (
        select(
            F.feedback_id,
            F.log_id,
            F.helpfulness_rating,
            F.feedback_text,
            F.created_at,
        )
        .where(
            F.helpfulness_rating <= low,
            F.feedback_text.is_not(None),
            func.length(func.trim(F.feedback_text)) > 0,
        )
        .order_by(F.created_at.desc(), F.feedback_id.desc())
        .limit(top_comments_limit)
    )
    comment_rows = (await db.execute(comments_stmt)).all()
    top_negative_comments = [
        {
            "feedback_id": int(r.feedback_id),
            "log_id": int(r.log_id),
            "helpfulness_rating": int(r.helpfulness_rating),
            "feedback_text": r.feedback_text,
            "created_at": r.created_at,
        }
        for r in comment_rows
    ]

    # ----- breakdown by triage level ----------------------------------------
    by_triage_stmt = (
        select(
            L.triage_level,
            func.count(F.feedback_id).label("count"),
            func.coalesce(func.avg(F.helpfulness_rating), 0.0).label("avg"),
        )
        .join(L, L.log_id == F.log_id)
        .group_by(L.triage_level)
        .order_by(func.count(F.feedback_id).desc())
    )
    triage_rows = (await db.execute(by_triage_stmt)).all()
    feedback_by_triage = [
        {
            "triage_level": r.triage_level,
            "count": int(r.count),
            "average_rating": round(float(r.avg or 0.0), 2),
        }
        for r in triage_rows
    ]

    # ----- breakdown by predicted (main) condition --------------------------
    by_cond_stmt = (
        select(
            L.predicted_condition,
            func.count(F.feedback_id).label("count"),
            func.coalesce(func.avg(F.helpfulness_rating), 0.0).label("avg"),
        )
        .join(L, L.log_id == F.log_id)
        .where(L.predicted_condition.is_not(None))
        .group_by(L.predicted_condition)
        .order_by(func.count(F.feedback_id).desc())
        .limit(20)
    )
    cond_rows = (await db.execute(by_cond_stmt)).all()
    feedback_by_main_condition = [
        {
            "main_condition": r.predicted_condition,
            "count": int(r.count),
            "average_rating": round(float(r.avg or 0.0), 2),
        }
        for r in cond_rows
    ]

    return {
        "total_feedback": total,
        "average_rating": average_rating,
        "rating_distribution": rating_distribution,
        "low_rating_count": low_rating_count,
        "recent_low_rated_cases": recent_low_rated_cases,
        "top_negative_comments": top_negative_comments,
        "feedback_by_triage": feedback_by_triage,
        "feedback_by_main_condition": feedback_by_main_condition,
    }
