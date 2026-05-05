"""
Admin CRUD repository for the ``disease_kb`` table.

Distinct from :mod:`app.services.kb_service`, which is a *read-side*
helper used by the prediction pipeline (case-insensitive lookup with
language fallback). This module owns paginated listing, point-reads by
id, inserts, and partial updates for the admin panel.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DiseaseKB


# --------------------------------------------------------------------------- #
# Reads                                                                        #
# --------------------------------------------------------------------------- #
async def list_diseases(
    db: AsyncSession,
    *,
    search: str | None = None,
    limit: int,
    offset: int,
) -> tuple[Sequence[DiseaseKB], int]:
    """
    Paginated list, optionally filtered by case-insensitive substring on
    the English OR Urdu name.
    """
    base_filter = None
    if search:
        needle = f"%{search.strip().lower()}%"
        base_filter = or_(
            func.lower(DiseaseKB.name_en).like(needle),
            func.lower(DiseaseKB.name_ur).like(needle),
        )

    items_stmt = (
        select(DiseaseKB)
        .order_by(DiseaseKB.name_en.asc(), DiseaseKB.disease_id.asc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count(DiseaseKB.disease_id))

    if base_filter is not None:
        items_stmt = items_stmt.where(base_filter)
        count_stmt = count_stmt.where(base_filter)

    items = (await db.execute(items_stmt)).scalars().all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return items, total


async def get_by_id(db: AsyncSession, disease_id: int) -> DiseaseKB | None:
    return await db.get(DiseaseKB, disease_id)


async def get_by_name_en(db: AsyncSession, name_en: str) -> DiseaseKB | None:
    """Used for duplicate-name guards on insert."""
    stmt = select(DiseaseKB).where(
        func.lower(DiseaseKB.name_en) == name_en.strip().lower()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Writes                                                                       #
# --------------------------------------------------------------------------- #
async def create(db: AsyncSession, *, data: dict[str, Any]) -> DiseaseKB:
    row = DiseaseKB(**data, updated_at=datetime.now(timezone.utc))
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise
    await db.refresh(row)
    return row


async def update(
    db: AsyncSession, *, row: DiseaseKB, changes: dict[str, Any],
) -> DiseaseKB:
    """Apply only the keys present in ``changes`` and bump ``updated_at``."""
    for field, value in changes.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


async def delete(db: AsyncSession, *, row: DiseaseKB) -> None:
    await db.delete(row)
    await db.commit()
