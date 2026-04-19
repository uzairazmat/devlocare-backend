import ssl

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text, event
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Base

logger = get_logger(__name__)

engine_kwargs: dict = {"echo": False}

if settings.is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    engine_kwargs.update(
        connect_args={"ssl": ssl_ctx},
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
    )

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)


if settings.is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create tables (SQLite) or verify connection (PostgreSQL)."""
    if settings.is_sqlite:
        logger.info("Creating SQLite tables (if they don't exist)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("SQLite database ready.")
    else:
        logger.info("Verifying database connection...")
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful.")


async def get_db():
    """
    FastAPI dependency that yields an async DB session.
    Rolls back on unexpected DB errors; domain exceptions (HTTP 4xx) are
    allowed to propagate quietly without polluting the error log.
    """
    from sqlalchemy.exc import SQLAlchemyError

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except SQLAlchemyError as e:
            await session.rollback()
            logger.exception("DB error — rolled back: %s", e)
            raise
        except Exception:
            await session.rollback()
            raise
