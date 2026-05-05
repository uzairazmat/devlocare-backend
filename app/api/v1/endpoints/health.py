from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db.session import get_db
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health", tags=["Health"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Health check endpoint.
    Pings the database to confirm connectivity.
    Returns 503 if the DB is unreachable.
    """
    logger.info("Health check requested.")
    try:
        await db.execute(text("SELECT 1"))
        logger.info("Health check passed — DB reachable.")
        return {"status": "healthy", "db": "connected"}
    except Exception as e:
        logger.exception("Health check failed — DB unreachable: %s", e)
        raise HTTPException(status_code=503, detail="Database unreachable")
