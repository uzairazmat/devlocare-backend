"""
Custom exceptions and global exception handlers.

Services raise domain exceptions (AppException subclasses) without knowing
about HTTP. The handlers in this module translate them into proper HTTP
responses with consistent JSON envelope: {"detail": "...", "code": "..."}.
"""
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Domain exceptions                                                            #
# --------------------------------------------------------------------------- #
class AppException(Exception):
    """Base class for all domain-level exceptions."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"
    message: str = "Application error"

    def __init__(self, message: str | None = None, code: str | None = None):
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


class NotFoundError(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found"


class ConflictError(AppException):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Resource already exists"


class ValidationError(AppException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "Invalid data"


class AuthenticationError(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_error"
    message = "Authentication failed"


class AuthorizationError(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    code = "authorization_error"
    message = "Not authorized"


# --------------------------------------------------------------------------- #
# Handler registration                                                         #
# --------------------------------------------------------------------------- #
def register_exception_handlers(app: FastAPI) -> None:
    """Attach global handlers to the FastAPI app."""

    @app.exception_handler(AppException)
    async def handle_app_exception(_: Request, exc: AppException):
        logger.warning(
            "AppException [%s] %s: %s",
            exc.status_code, exc.code, exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": "http_error"},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError):
        errors = jsonable_encoder(
            exc.errors(), custom_encoder={bytes: lambda b: b.decode("utf-8", "replace")}
        )
        logger.info("Request validation failed: %s", errors)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed",
                "code": "validation_error",
                "errors": errors,
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("Database error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Database error", "code": "database_error"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "code": "internal_error"},
        )
