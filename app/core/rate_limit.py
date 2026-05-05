"""
Rate-limiting setup (FRD §6 + Tools section — slowapi).

Single shared `Limiter` instance. Endpoints import this module and decorate
their routes via ``@limiter.limit(settings.RATE_LIMIT_LOGIN)`` etc. The
limiter key is the client IP (`get_remote_address`); behind a proxy that
will be the proxy IP unless the proxy injects ``X-Forwarded-For`` and the
ASGI server is configured to trust it.

When ``settings.RATE_LIMIT_ENABLED`` is False (e.g. in tests) the limiter
is replaced with a no-op so decorators stay compile-clean.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.RATE_LIMIT_ENABLED,
    headers_enabled=True,
    default_limits=[],
)


def rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Convert slowapi's ``RateLimitExceeded`` into our normalised error envelope
    so the frontend's `ApiError` mapper can read it the same way it reads
    every other API error.
    """
    detail = (
        getattr(exc, "detail", None)
        if isinstance(exc, RateLimitExceeded)
        else "Rate limit exceeded"
    )
    return JSONResponse(
        status_code=429,
        content={
            "code": "rate_limited",
            "message": "Too many requests — please slow down and try again.",
            "detail": str(detail),
        },
    )
