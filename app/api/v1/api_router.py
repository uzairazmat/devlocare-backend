from fastapi import APIRouter

from app.api.v1.endpoints import auth, health

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth")
