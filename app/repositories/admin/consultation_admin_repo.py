"""
Admin-side consultation repository.

Distinct from :mod:`app.repositories.log_repo` because admin queries are
*not* scoped to a single user. They read across the whole ``symptom_logs``
table, optionally joined to ``users`` (for the username column) and to
``consultation_feedback`` (so the detail endpoint can surface any
attached rating without a second round-trip).

Filters supported by the list endpoint:
* ``disease``  — case-insensitive substring on ``predicted_condition``.
* ``triage``   — exact match on ``triage_level``.
* ``language`` — matches against ``users.language_pref``; rows with
  ``user_id IS NULL`` are treated as English (matching the rule used
  inside ``prediction_service``).
"""
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConsultationFeedback, SymptomLog, User


# --------------------------------------------------------------------------- #
# Filter helpers                                                               #
# --------------------------------------------------------------------------- #
def _apply_filters(
    stmt: Select,
    *,
    disease: str | None,
    triage: str | None,
    language: str | None,
) -> Select:
    if disease:
        needle = f"%{disease.strip().lower()}%"
        stmt = stmt.where(func.lower(SymptomLog.predicted_condition).like(needle))

    if triage:
        stmt = stmt.where(SymptomLog.triage_level == triage)

    if language:
        if language.lower() == "english":
            # Guests count as English too.
            stmt = stmt.where(
                or_(User.language_pref == "English", SymptomLog.user_id.is_(None))
            )
        else:
            stmt = stmt.where(User.language_pref == language)

    return stmt


# --------------------------------------------------------------------------- #
# List (paginated, filterable)                                                 #
# --------------------------------------------------------------------------- #
async def list_consultations(
    db: AsyncSession,
    *,
    disease: str | None = None,
    triage: str | None = None,
    language: str | None = None,
    limit: int,
    offset: int,
) -> tuple[Sequence[Any], int]:
    """
    Return ``(rows, total)`` where each row is a (SymptomLog, User) tuple.
    The total is computed with the same filters but without limit/offset
    so the UI can render an accurate "X of Y" badge.
    """
    base = (
        select(SymptomLog, User)
        .select_from(SymptomLog)
        .join(User, User.user_id == SymptomLog.user_id, isouter=True)
    )
    base = _apply_filters(
        base, disease=disease, triage=triage, language=language,
    )

    items_stmt = (
        base.order_by(SymptomLog.created_at.desc(), SymptomLog.log_id.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = (
        select(func.count(SymptomLog.log_id))
        .select_from(SymptomLog)
        .join(User, User.user_id == SymptomLog.user_id, isouter=True)
    )
    count_stmt = _apply_filters(
        count_stmt, disease=disease, triage=triage, language=language,
    )

    items = (await db.execute(items_stmt)).all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return items, total


# --------------------------------------------------------------------------- #
# Detail (single log + user + latest feedback)                                 #
# --------------------------------------------------------------------------- #
async def get_consultation_detail(
    db: AsyncSession, *, log_id: int,
) -> tuple[SymptomLog, User | None, ConsultationFeedback | None] | None:
    """
    Fetch one consultation along with its owning user (if any) and the
    most recent feedback row (if any). One round-trip via outer joins.
    """
    stmt = (
        select(SymptomLog, User, ConsultationFeedback)
        .select_from(SymptomLog)
        .join(User, User.user_id == SymptomLog.user_id, isouter=True)
        .join(
            ConsultationFeedback,
            ConsultationFeedback.log_id == SymptomLog.log_id,
            isouter=True,
        )
        .where(SymptomLog.log_id == log_id)
        .order_by(ConsultationFeedback.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1], row[2]
