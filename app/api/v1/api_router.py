from fastapi import APIRouter

from app.api.v1.endpoints import auth, feedback, health, history, predict
from app.api.v1.endpoints.admin import admin_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(predict.router, prefix="/predict")
api_router.include_router(history.router, prefix="/history")
api_router.include_router(feedback.router, prefix="/feedback")
api_router.include_router(admin_router)
