"""
Admin Panel sub-router.

Combines the four admin modules under a single ``/admin`` prefix:

    /api/v1/admin/dashboard/...
    /api/v1/admin/consultations/...
    /api/v1/admin/feedback/...
    /api/v1/admin/diseases/...
"""
from fastapi import APIRouter

from app.api.v1.endpoints.admin import (
    consultations,
    dashboard,
    feedback,
    knowledge_base,
    users,
)

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard.router, prefix="/dashboard")
admin_router.include_router(consultations.router, prefix="/consultations")
admin_router.include_router(feedback.router, prefix="/feedback")
admin_router.include_router(knowledge_base.router, prefix="/diseases")
admin_router.include_router(users.router, prefix="/users")
