"""
Admin knowledge-base service layer.

Translates DB rows into Pydantic ``DiseaseKBItem`` objects and enforces
the admin business rules:

* Inserting a disease whose ``name_en`` already exists returns 409.
* PUT updates ignore ``None`` keys so partial payloads cannot wipe data.
* Deleting a non-existent row returns 404.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.admin import (
    DiseaseKBCreateRequest,
    DiseaseKBItem,
    DiseaseKBListResponse,
    DiseaseKBUpdateRequest,
)
from app.repositories.admin import kb_repo

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Reads                                                                        #
# --------------------------------------------------------------------------- #
async def list_diseases(
    db: AsyncSession,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> DiseaseKBListResponse:
    rows, total = await kb_repo.list_diseases(
        db, search=search, limit=limit, offset=offset,
    )
    items = [DiseaseKBItem.model_validate(r) for r in rows]
    return DiseaseKBListResponse(
        items=items,
        total=total,
        has_more=(offset + len(items)) < total,
        limit=limit,
        offset=offset,
    )


async def get_disease(db: AsyncSession, disease_id: int) -> DiseaseKBItem:
    row = await kb_repo.get_by_id(db, disease_id)
    if row is None:
        raise NotFoundError(
            message=f"Disease {disease_id} not found",
            code="disease_not_found",
        )
    return DiseaseKBItem.model_validate(row)


# --------------------------------------------------------------------------- #
# Writes                                                                       #
# --------------------------------------------------------------------------- #
async def create_disease(
    db: AsyncSession, payload: DiseaseKBCreateRequest,
) -> DiseaseKBItem:
    existing = await kb_repo.get_by_name_en(db, payload.name_en)
    if existing is not None:
        raise ConflictError(
            message=f"Disease '{payload.name_en}' already exists",
            code="disease_already_exists",
        )

    try:
        row = await kb_repo.create(db, data=payload.model_dump())
    except IntegrityError as exc:
        # Race condition: a duplicate slipped past the pre-check between
        # the SELECT and the INSERT. Translate into a clean 409.
        logger.warning("KB insert hit IntegrityError: %s", exc)
        raise ConflictError(
            message=f"Disease '{payload.name_en}' already exists",
            code="disease_already_exists",
        ) from exc

    logger.info(
        "AUDIT admin.kb.create disease_id=%s name_en=%s",
        row.disease_id, row.name_en,
    )
    return DiseaseKBItem.model_validate(row)


async def update_disease(
    db: AsyncSession,
    disease_id: int,
    payload: DiseaseKBUpdateRequest,
) -> DiseaseKBItem:
    row = await kb_repo.get_by_id(db, disease_id)
    if row is None:
        raise NotFoundError(
            message=f"Disease {disease_id} not found",
            code="disease_not_found",
        )

    # Only carry non-None keys into the patch — guard against accidental wipes.
    changes = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None
    }
    if not changes:
        return DiseaseKBItem.model_validate(row)

    updated = await kb_repo.update(db, row=row, changes=changes)
    logger.info(
        "AUDIT admin.kb.update disease_id=%s fields=%s",
        updated.disease_id, sorted(changes.keys()),
    )
    return DiseaseKBItem.model_validate(updated)


async def delete_disease(db: AsyncSession, disease_id: int) -> None:
    row = await kb_repo.get_by_id(db, disease_id)
    if row is None:
        raise NotFoundError(
            message=f"Disease {disease_id} not found",
            code="disease_not_found",
        )
    await kb_repo.delete(db, row=row)
    logger.info("AUDIT admin.kb.delete disease_id=%s", disease_id)
