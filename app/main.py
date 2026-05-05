from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.api_router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger
from app.core.rate_limit import limiter, rate_limit_handler
from app.db.session import engine, init_db
from app.services.admin.bootstrap import ensure_super_admin

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting %s v%s (%s)",
                settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    await init_db()
    # One-shot super-admin bootstrap. Runs every boot but is a no-op once
    # the row exists. There is intentionally NO API path to mint a super
    # admin — credentials come from SUPER_ADMIN_* env vars only.
    await ensure_super_admin()
    logger.info("%s is ready.", settings.APP_NAME)
    try:
        yield
    finally:
        logger.info("Shutting down %s...", settings.APP_NAME)
        await engine.dispose()
        logger.info("Database engine disposed.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# Rate limiting — bind the shared limiter to the app and register the
# 429 handler before exception handlers so it wins for RateLimitExceeded.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS — allow the SPA to call the API across origins. Origins are read
# from settings.CORS_ORIGINS (comma-separated). Use "*" only in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length", "X-Export-Rows"],
)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }
