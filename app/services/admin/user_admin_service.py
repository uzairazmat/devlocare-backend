"""
Admin user-management service.

Owns the business rules around the three super-admin-only routes:

* ``POST /admin/users/create-admin``     — mint a new admin
* ``POST /admin/users/{user_id}/promote`` — normal user → admin
* ``POST /admin/users/{user_id}/demote``  — admin → normal user

Hard invariants enforced here (never relaxed):

1. Super-admin status is **never** granted or revoked through the API.
2. The super admin row cannot be demoted via this service.
3. A super admin cannot demote themselves (locks themselves out — the
   self-check is a defence-in-depth anyway).
4. Username / email uniqueness is enforced before insert.
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import get_password_hash
from app.db.models import User
from app.models.admin import CreateAdminRequest
from app.models.response import UserResponse
from app.repositories import user_repo

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Create                                                                       #
# --------------------------------------------------------------------------- #
async def create_admin(
    db: AsyncSession,
    actor: User,
    payload: CreateAdminRequest,
) -> UserResponse:
    """
    Create a brand-new user with ``is_admin=True``.

    The caller is guaranteed to be a super admin by the route-level
    ``require_super_admin`` dependency, but we accept ``actor`` here for
    audit logging.
    """
    if await user_repo.get_by_username(db, payload.username):
        raise ConflictError(
            message=f"Username '{payload.username}' is already taken",
            code="username_taken",
        )
    if await user_repo.get_by_email(db, payload.email):
        raise ConflictError(
            message=f"Email '{payload.email}' is already registered",
            code="email_taken",
        )

    new_admin = User(
        username=payload.username,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        is_admin=True,
        is_super_admin=False,
    )
    try:
        new_admin = await user_repo.create(db, new_admin)
    except IntegrityError as exc:
        # Race: a duplicate slipped past the pre-check. Translate cleanly.
        await db.rollback()
        raise ConflictError(
            message="Username or email already taken",
            code="user_already_exists",
        ) from exc

    logger.info(
        "AUDIT admin.create actor_id=%s actor=%s new_user_id=%s username=%s",
        actor.user_id, actor.username, new_admin.user_id, new_admin.username,
    )
    return UserResponse.model_validate(new_admin)


# --------------------------------------------------------------------------- #
# Promote / demote                                                             #
# --------------------------------------------------------------------------- #
async def promote_to_admin(
    db: AsyncSession,
    actor: User,
    target_user_id: int,
) -> UserResponse:
    """Flip ``is_admin`` to ``True`` on an existing user. No-op if already admin."""
    target = await _load_target(db, target_user_id)

    if target.is_super_admin:
        # Already strictly above admin; promoting is meaningless.
        return UserResponse.model_validate(target)
    if target.is_admin:
        return UserResponse.model_validate(target)

    target.is_admin = True
    target = await user_repo.update(db, target)
    logger.info(
        "AUDIT admin.promote actor_id=%s actor=%s target_user_id=%s username=%s",
        actor.user_id, actor.username, target.user_id, target.username,
    )
    return UserResponse.model_validate(target)


async def demote_from_admin(
    db: AsyncSession,
    actor: User,
    target_user_id: int,
) -> UserResponse:
    """
    Flip ``is_admin`` to ``False``. Refuses to demote a super admin and
    refuses self-demotion.
    """
    if actor.user_id == target_user_id:
        raise ValidationError(
            message="You cannot demote yourself",
            code="cannot_self_demote",
        )

    target = await _load_target(db, target_user_id)

    if target.is_super_admin:
        # Hard invariant: super-admin rights are immutable via API. The
        # only way to revoke them is direct DB access by an operator.
        raise AuthorizationError(
            message="Super admin cannot be demoted via API",
            code="cannot_demote_super_admin",
        )

    if not target.is_admin:
        # Idempotent — already a normal user.
        return UserResponse.model_validate(target)

    target.is_admin = False
    target = await user_repo.update(db, target)
    logger.info(
        "AUDIT admin.demote actor_id=%s actor=%s target_user_id=%s username=%s",
        actor.user_id, actor.username, target.user_id, target.username,
    )
    return UserResponse.model_validate(target)


# --------------------------------------------------------------------------- #
# Internal                                                                     #
# --------------------------------------------------------------------------- #
async def _load_target(db: AsyncSession, user_id: int) -> User:
    target = await user_repo.get_by_id(db, user_id)
    if target is None:
        raise NotFoundError(
            message=f"User {user_id} not found",
            code="user_not_found",
        )
    return target
