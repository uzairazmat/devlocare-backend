"""
Admin knowledge-base CRUD endpoints — mounted under ``/api/v1/admin/diseases``.

* ``GET    /``                    — paginated list (optional ``search``)
* ``GET    /{disease_id}``        — fetch one row
* ``POST   /``                    — add a new disease entry
* ``PUT    /{disease_id}``        — partial update (only provided fields)
* ``DELETE /{disease_id}``        — remove an entry
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.admin import (
    DiseaseKBCreateRequest,
    DiseaseKBItem,
    DiseaseKBListResponse,
    DiseaseKBUpdateRequest,
)
from app.models.response import MessageResponse
from app.services.admin import kb_admin_service

logger = get_logger(__name__)
router = APIRouter(tags=["Admin · Knowledge Base"])

_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


@router.get(
    "",
    response_model=DiseaseKBListResponse,
    summary="List all diseases (paginated, searchable)",
)
async def list_diseases(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DiseaseKBListResponse:
    return await kb_admin_service.list_diseases(
        db, search=search, limit=limit, offset=offset,
    )


@router.get(
    "/{disease_id}",
    response_model=DiseaseKBItem,
    summary="Get a disease by id",
)
async def get_disease(
    disease_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DiseaseKBItem:
    return await kb_admin_service.get_disease(db, disease_id)


@router.post(
    "",
    response_model=DiseaseKBItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new disease entry",
)
async def create_disease(
    payload: DiseaseKBCreateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DiseaseKBItem:
    return await kb_admin_service.create_disease(db, payload)


@router.put(
    "/{disease_id}",
    response_model=DiseaseKBItem,
    summary="Update an existing disease entry (partial)",
)
async def update_disease(
    disease_id: int,
    payload: DiseaseKBUpdateRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> DiseaseKBItem:
    return await kb_admin_service.update_disease(db, disease_id, payload)


@router.delete(
    "/{disease_id}",
    response_model=MessageResponse,
    summary="Delete a disease entry",
)
async def delete_disease(
    disease_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await kb_admin_service.delete_disease(db, disease_id)
    return MessageResponse(message=f"Disease {disease_id} deleted successfully")
