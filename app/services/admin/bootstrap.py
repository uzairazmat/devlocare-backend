"""
One-shot super-admin bootstrap.

Runs once on every application startup but is a no-op as soon as a
``users`` row with ``is_super_admin=True`` exists. The credentials come
from environment variables only — there is no API path to mint a super
admin, by design:

* ``SUPER_ADMIN_USERNAME``
* ``SUPER_ADMIN_PASSWORD``
* ``SUPER_ADMIN_EMAIL``

If any required env var is missing the bootstrap is skipped with a clear
warning (useful for read-only / migration containers). Existing rows are
NEVER mutated — if the desired username already exists as a normal user,
the bootstrap aborts loudly so an operator can resolve the conflict.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import get_password_hash
from app.db.models import User
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)


async def ensure_super_admin() -> None:
    """
    Create the super admin if and only if none exists yet.

    Safe to call on every boot. Logs at INFO when it acts and at DEBUG
    when it's a no-op so production startup stays quiet.
    """
    async with AsyncSessionLocal() as db:
        existing = await _get_super_admin(db)
        if existing is not None:
            logger.debug(
                "Super admin already present: user_id=%s username=%s — skipping bootstrap",
                existing.user_id, existing.username,
            )
            return

        username = (settings.SUPER_ADMIN_USERNAME or "").strip()
        password = settings.SUPER_ADMIN_PASSWORD or ""
        email = (settings.SUPER_ADMIN_EMAIL or "").strip()

        if not username or not password or not email:
            logger.warning(
                "Super admin bootstrap SKIPPED — set SUPER_ADMIN_USERNAME, "
                "SUPER_ADMIN_PASSWORD and SUPER_ADMIN_EMAIL in .env to enable."
            )
            return

        # Refuse to silently take over an existing username/email — that
        # would be a privilege escalation. An operator must rename the env
        # values or clean up the conflicting row by hand.
        clash = await _get_by_username_or_email(db, username, email)
        if clash is not None:
            logger.error(
                "Super admin bootstrap ABORTED — a user with the requested "
                "username/email already exists (user_id=%s, username=%s). "
                "Resolve the conflict in the DB or change SUPER_ADMIN_* env.",
                clash.user_id, clash.username,
            )
            return

        super_admin = User(
            username=username,
            email=email,
            password_hash=get_password_hash(password),
            is_admin=True,
            is_super_admin=True,
        )
        db.add(super_admin)
        await db.commit()
        await db.refresh(super_admin)

        logger.info(
            "Super admin bootstrapped: user_id=%s username=%s",
            super_admin.user_id, super_admin.username,
        )


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #
async def _get_super_admin(db: AsyncSession) -> User | None:
    stmt = select(User).where(User.is_super_admin.is_(True)).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_by_username_or_email(
    db: AsyncSession, username: str, email: str,
) -> User | None:
    stmt = (
        select(User)
        .where(or_(User.username == username, User.email == email))
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
