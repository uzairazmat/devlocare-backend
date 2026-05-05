"""
Security utilities: password hashing (bcrypt) and JWT token handling.
Kept free of any framework-specific imports so it can be unit-tested easily.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

BCRYPT_MAX_BYTES = 72


class TokenError(Exception):
    """Raised when a JWT token is invalid, expired, or malformed."""


def _encode_password(password: str) -> bytes:
    """
    Encode password to bytes and clip to bcrypt's 72-byte limit.
    bcrypt silently ignores bytes beyond 72 anyway — we truncate explicitly
    so both hash and verify stay consistent.
    """
    return password.encode("utf-8")[:BCRYPT_MAX_BYTES]


def get_password_hash(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    hashed = bcrypt.hashpw(_encode_password(password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _encode_password(plain_password),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError) as e:
        logger.warning("Password verification failed: %s", e)
        return False


def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject: Usually the user_id (stored in the `sub` claim).
        expires_delta: Optional override for token lifetime.
        extra_claims: Extra JWT claims to embed (e.g. username, role).

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Raises:
        TokenError: if the token is expired, malformed, or signed with a
                    different key / algorithm.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except ExpiredSignatureError as e:
        logger.warning("Rejected expired token")
        raise TokenError("Token has expired") from e
    except InvalidTokenError as e:
        logger.warning("Rejected invalid token: %s", e)
        raise TokenError("Invalid token") from e
