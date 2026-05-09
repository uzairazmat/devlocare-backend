"""
User repository — all DB access for the users table lives here.
Services call these functions; they never touch SQLAlchemy directly.
"""
from typing import Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()


async def get_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_username_or_email(
    db: AsyncSession, identifier: str
) -> User | None:
    """Look up a user by either username OR email (case sensitive)."""
    result = await db.execute(
        select(User).where(
            or_(User.username == identifier, User.email == identifier)
        )
    )
    return result.scalar_one_or_none()


async def create(db: AsyncSession, user: User) -> User:
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update(db: AsyncSession, user: User) -> User:
    await db.commit()
    await db.refresh(user)
    return user


def _apply_user_filters(
    stmt: Select,
    *,
    role: str | None,
    search: str | None,
) -> Select:
    """Shared role + search filter used by list_users / count_users."""
    if role == "admins":
        # Treat super-admins as admins (strict superset).
        stmt = stmt.where(
            or_(User.is_admin.is_(True), User.is_super_admin.is_(True))
        )
    elif role == "users":
        stmt = stmt.where(
            User.is_admin.is_(False), User.is_super_admin.is_(False)
        )

    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.username).like(needle),
                func.lower(User.email).like(needle),
            )
        )
    return stmt


async def list_users(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    role: str | None = None,
    search: str | None = None,
) -> tuple[Sequence[User], int]:
    """
    Return ``(rows, total)`` for the admin user-management screen.

    ``role`` is ``"admins"``, ``"users"`` or ``None`` (all).
    ``search`` matches a case-insensitive substring on username OR email.
    """
    base = select(User)
    base = _apply_user_filters(base, role=role, search=search)

    items_stmt = (
        base.order_by(User.created_at.desc(), User.user_id.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = _apply_user_filters(
        select(func.count(User.user_id)), role=role, search=search,
    )

    items = (await db.execute(items_stmt)).scalars().all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return items, total
