"""
Shared FastAPI dependencies for v1 routes.
"""
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.services import auth_service

logger = get_logger(__name__)

# Strict scheme: missing / bad token → 401 automatically.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token", auto_error=True
)

# Lax scheme: missing token returns None so guests can hit public endpoints.
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token", auto_error=False
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolve the authenticated user from the Authorization header.
    Raises AuthenticationError (401) on any problem.
    """
    return await auth_service.get_user_from_token(db, token)


async def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Same as `get_current_user`, but returns `None` when:
      * the request has no Authorization header, OR
      * the token is present but invalid / expired.

    This is what "guest" endpoints such as POST /predict/text use — a
    logged-in user gets their log attached to their account, anonymous
    callers still get a prediction (with `user_id = NULL`).
    """
    if not token:
        return None
    try:
        return await auth_service.get_user_from_token(db, token)
    except AppException as exc:
        logger.debug("Optional auth ignored invalid token: %s", exc.message)
        return None
