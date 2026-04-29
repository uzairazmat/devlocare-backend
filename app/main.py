from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.api_router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger
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

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }
