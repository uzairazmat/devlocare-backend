"""
Auth & user management endpoints (mounted under /api/v1/auth).
"""
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.request import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserUpdateRequest,
)
from app.models.response import UserResponse
from app.services import auth_service

logger = get_logger(__name__)
router = APIRouter(tags=["Auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    payload: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Create a new user account and return an access token immediately so
    the client can proceed without a second round-trip to /login.
    """
    return await auth_service.register_user(db, payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with username or email + password (JSON)",
)
async def login(
    payload: UserLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate a user and return a JWT access token."""
    return await auth_service.authenticate_user(db, payload)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 password flow login (form-encoded, for Swagger Authorize)",
)
async def login_oauth2(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Same as /login, but accepts form-encoded data so the Swagger UI
    "Authorize" button can use it. `username` can be either a username
    or email address.
    """
    payload = UserLoginRequest(
        username_or_email=form_data.username,
        password=form_data.password,
    )
    return await auth_service.authenticate_user(db, payload)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current authenticated user",
)
async def read_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update the current user's profile (age, sex, language_pref)",
)
async def update_me(
    payload: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await auth_service.update_user_profile(db, current_user, payload)
