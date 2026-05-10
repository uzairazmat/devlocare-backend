"""
Symptom-log repository — all DB access for the `symptom_logs` table.

Owns CRUD for `SymptomLog` and the read-side queries powering UC-06
(history sidebar + PDF export). Every history/export query is scoped to a
single `user_id` so ownership is enforced at the SQL layer; the API layer
never sees rows that don't belong to the caller.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SymptomLog


async def create(db: AsyncSession, log: SymptomLog) -> SymptomLog:
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def get_by_id(db: AsyncSession, log_id: int) -> SymptomLog | None:
    return await db.get(SymptomLog, log_id)


async def save(db: AsyncSession, log: SymptomLog) -> SymptomLog:
    """
    Commit pending changes on an attached SymptomLog and refresh its state.
    Used by the stateful chat flow to persist intermediate updates
    (chat_history appends, prediction finalisation) without re-adding the
    row to the session.
    """
    await db.commit()
    await db.refresh(log)
    return log


# --------------------------------------------------------------------------- #
# UC-06 — History sidebar                                                      #
# --------------------------------------------------------------------------- #
async def get_user_history(
    db: AsyncSession,
    *,
    user_id: int,
    limit: int,
    offset: int,
) -> tuple[Sequence[SymptomLog], int]:
    """
    Return ``(items, total)`` for the authenticated user's consultation
    history, sorted newest-first.

    A separate ``COUNT(*)`` is run so the API can compute ``has_more`` and
    show "X / Y consultations" in the sidebar without paging through the
    whole table.
    """
    base_filter = SymptomLog.user_id == user_id

    items_stmt = (
        select(SymptomLog)
        .where(base_filter)
        .order_by(SymptomLog.created_at.desc(), SymptomLog.log_id.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count(SymptomLog.log_id)).where(base_filter)

    items_result = await db.execute(items_stmt)
    count_result = await db.execute(count_stmt)

    items = items_result.scalars().all()
    total = int(count_result.scalar_one() or 0)
    return items, total


# --------------------------------------------------------------------------- #
# UC-06 — Single-log delete with ownership guard                               #
# --------------------------------------------------------------------------- #
async def delete_log(
    db: AsyncSession,
    *,
    log_id: int,
    user_id: int,
) -> int:
    """
    Permanently delete the consultation identified by ``log_id`` *only* if
    it belongs to ``user_id``.

    Returns the number of rows actually deleted (0 = not found OR not owned;
    1 = success). The caller is responsible for translating 0 into the
    appropriate HTTP error.
    """
    stmt = delete(SymptomLog).where(
        SymptomLog.log_id == log_id,
        SymptomLog.user_id == user_id,
    )
    result = await db.execute(stmt)
    await db.commit()
    return int(result.rowcount or 0)


# --------------------------------------------------------------------------- #
# UC-06 — Export selection                                                     #
# --------------------------------------------------------------------------- #
async def get_logs_for_export(
    db: AsyncSession,
    *,
    user_id: int,
    log_ids: list[int] | None = None,
    last_n: int | None = None,
) -> Sequence[SymptomLog]:
    """
    Fetch the logs to include in a PDF export bundle.

    Exactly one of ``log_ids`` / ``last_n`` must be supplied:

    * ``log_ids`` — return every requested log that belongs to the user. IDs
      not owned by the user are silently dropped (no leakage).
    * ``last_n``  — return the user's N most recent logs.

    Results are always sorted newest-first to match the on-screen sidebar
    order.
    """
    if (log_ids is None) == (last_n is None):
        raise ValueError("Provide exactly one of log_ids or last_n")

    stmt = (
        select(SymptomLog)
        .where(SymptomLog.user_id == user_id)
        .order_by(SymptomLog.created_at.desc(), SymptomLog.log_id.desc())
    )

    if log_ids is not None:
        stmt = stmt.where(SymptomLog.log_id.in_(log_ids))
    else:
        stmt = stmt.limit(int(last_n))  # type: ignore[arg-type]

    result = await db.execute(stmt)
    return result.scalars().all()
