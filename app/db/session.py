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
        await _ensure_user_role_columns()
        logger.info("SQLite database ready.")
    else:
        logger.info("Verifying database connection...")
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await _ensure_user_role_columns()
        logger.info("Database connection successful.")


async def _ensure_user_role_columns() -> None:
    """
    Lightweight, idempotent migration for the RBAC rollout.

    `Base.metadata.create_all` does not ALTER existing tables, so legacy
    DBs created before the role flags landed need a one-shot column add.
    Runs on every boot but is a no-op once the columns exist — cheap
    enough to keep until a real migration tool (Alembic) is introduced.
    """
    role_columns = {
        "is_admin": "BOOLEAN NOT NULL DEFAULT 0",
        "is_super_admin": "BOOLEAN NOT NULL DEFAULT 0",
    }

    async with engine.begin() as conn:
        if settings.is_sqlite:
            existing = {
                row[1]
                for row in (
                    await conn.exec_driver_sql("PRAGMA table_info(users)")
                ).all()
            }
        else:
            rows = (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'users'"
                    )
                )
            ).all()
            existing = {r[0] for r in rows}

        for col, ddl in role_columns.items():
            if col not in existing:
                logger.info("Migration: adding users.%s", col)
                await conn.exec_driver_sql(
                    f"ALTER TABLE users ADD COLUMN {col} {ddl}"
                )


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
