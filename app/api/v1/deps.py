"""
Shared FastAPI dependencies for v1 routes.
"""
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_db
from app.services import auth_service

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=True)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resolve the authenticated user from the Authorization header.
    Raises AuthenticationError (401) on any problem.
    """
    return await auth_service.get_user_from_token(db, token)
