"""
Super-admin-only user-management endpoints, mounted under
``/api/v1/admin/users``.

* ``POST /create-admin``         — provision a brand-new admin account.
* ``POST /{user_id}/promote``    — promote a normal user to admin.
* ``POST /{user_id}/demote``     — demote an admin back to a normal user.

Every route depends on :func:`require_super_admin`, so plain admins
explicitly cannot reach them. Super-admin status itself is never granted
or revoked through any API — it is reserved for the one-shot startup
bootstrap (see ``app/services/admin/bootstrap.py``).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_super_admin
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.admin import (
    AdminUserListResponse,
    CreateAdminRequest,
    UserRoleFilter,
)
from app.models.response import UserResponse
from app.services.admin import user_admin_service

logger = get_logger(__name__)
router = APIRouter(tags=["Admin · User Management"])


@router.get(
    "",
    response_model=AdminUserListResponse,
    summary="List users (super admin only)",
    description=(
        "Paginated list of every user on the platform. Filter by ``role`` "
        "(``admins`` includes super admins; ``users`` is non-admins only) "
        "and/or a case-insensitive ``search`` substring on username or email."
    ),
)
async def list_users(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    role: UserRoleFilter | None = Query(
        default=None,
        description="`admins` (admins + super admins) or `users` (everyone else).",
    ),
    search: str | None = Query(
        default=None,
        max_length=100,
        description="Case-insensitive substring match on username or email.",
    ),
    actor: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    return await user_admin_service.list_users(
        db, limit=limit, offset=offset, role=role, search=search,
    )


@router.post(
    "/create-admin",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new admin user (super admin only)",
)
async def create_admin(
    payload: CreateAdminRequest,
    actor: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_admin_service.create_admin(db, actor, payload)


@router.post(
    "/{user_id}/promote",
    response_model=UserResponse,
    summary="Promote a normal user to admin (super admin only)",
)
async def promote_user(
    user_id: int,
    actor: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_admin_service.promote_to_admin(db, actor, user_id)


@router.post(
    "/{user_id}/demote",
    response_model=UserResponse,
    summary="Demote an admin back to normal user (super admin only)",
)
async def demote_user(
    user_id: int,
    actor: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await user_admin_service.demote_from_admin(db, actor, user_id)
