"""
Authentication & user management service layer.

All domain logic (hashing, validation, uniqueness checks) lives here so
the API layer stays thin and testable.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from app.db.models import User
from app.models.request import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserUpdateRequest,
)
from app.models.response import UserResponse
from app.repositories import user_repo

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Public service functions                                                     #
# --------------------------------------------------------------------------- #
async def register_user(
    db: AsyncSession, payload: UserRegisterRequest
) -> TokenResponse:
    """
    Register a new user. Raises ConflictError if username/email already taken.
    Returns a TokenResponse so the client can log in immediately.
    """
    logger.info("Registering new user: username=%s email=%s",
                payload.username, payload.email)

    if await user_repo.get_by_username(db, payload.username):
        logger.warning("Register rejected — username taken: %s", payload.username)
        raise ConflictError(
            message=f"Username '{payload.username}' is already taken",
            code="username_taken",
        )

    if await user_repo.get_by_email(db, payload.email):
        logger.warning("Register rejected — email taken: %s", payload.email)
        raise ConflictError(
            message=f"Email '{payload.email}' is already registered",
            code="email_taken",
        )

    new_user = User(
        username=payload.username,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        age=payload.age,
        sex=payload.sex,
        language_pref=payload.language_pref,
    )
    new_user = await user_repo.create(db, new_user)
    logger.info("User registered successfully: user_id=%s", new_user.user_id)

    return _build_token_response(new_user)


async def authenticate_user(
    db: AsyncSession, payload: UserLoginRequest
) -> TokenResponse:
    """
    Verify credentials and issue a JWT. Raises AuthenticationError on any
    failure (deliberately generic so we don't leak which field was wrong).
    """
    logger.info("Login attempt: identifier=%s", payload.username_or_email)

    user = await user_repo.get_by_username_or_email(
        db, payload.username_or_email
    )
    if not user or not verify_password(payload.password, user.password_hash):
        logger.warning(
            "Login failed for identifier=%s", payload.username_or_email
        )
        raise AuthenticationError(
            message="Invalid username/email or password",
            code="invalid_credentials",
        )

    logger.info("Login successful: user_id=%s", user.user_id)
    return _build_token_response(user)


async def update_user_profile(
    db: AsyncSession, user: User, payload: UserUpdateRequest
) -> UserResponse:
    """Apply partial updates (age, sex, language_pref) to a user."""
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not data:
        logger.info("Update called with no fields for user_id=%s", user.user_id)
        return UserResponse.model_validate(user)

    logger.info("Updating profile for user_id=%s fields=%s",
                user.user_id, list(data.keys()))

    for field, value in data.items():
        setattr(user, field, value)

    user = await user_repo.update(db, user)
    return UserResponse.model_validate(user)


async def get_user_from_token(db: AsyncSession, token: str) -> User:
    """
    Decode a JWT and return the active DB user.
    Raises AuthenticationError on any failure.
    """
    try:
        payload = decode_access_token(token)
    except TokenError as e:
        raise AuthenticationError(message=str(e), code="invalid_token") from e

    sub = payload.get("sub")
    if not sub:
        raise AuthenticationError(
            message="Token missing subject", code="invalid_token"
        )

    try:
        user_id = int(sub)
    except (TypeError, ValueError) as e:
        raise AuthenticationError(
            message="Invalid token subject", code="invalid_token"
        ) from e

    user = await user_repo.get_by_id(db, user_id)
    if not user:
        logger.warning("Token valid but user missing: user_id=%s", user_id)
        raise NotFoundError(message="User not found", code="user_not_found")

    return user


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #
def _build_token_response(user: User) -> TokenResponse:
    # Role flags are embedded in the JWT so middleware/routers can perform a
    # cheap claim check, but every admin dependency STILL re-reads the row
    # from the DB before granting access — frontend role flags and stale
    # tokens are never trusted as the final word.
    token = create_access_token(
        subject=user.user_id,
        extra_claims={
            "username": user.username,
            "is_admin": bool(user.is_admin),
            "is_super_admin": bool(user.is_super_admin),
        },
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )
