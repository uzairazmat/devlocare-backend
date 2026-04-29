"""
Symptom prediction endpoints (UC-01), mounted under /api/v1/predict.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user_optional
from app.core.logging import get_logger
from app.db.models import User
from app.db.session import get_db
from app.models.request import SymptomTextRequest
from app.models.response import PredictionResponse
from app.services import prediction_service

logger = get_logger(__name__)
router = APIRouter(tags=["Prediction"])


@router.post(
    "/text",
    response_model=PredictionResponse,
    summary="Predict possible conditions from a free-text symptom description",
    description=(
        "Accepts a 5-500 character symptom description plus optional patient "
        "metadata (age, sex, pregnancy, chronic disease, severity, duration). "
        "Returns the Top-3 predicted conditions with confidence scores, a "
        "triage level, recommended specialist, bilingual care tips and red "
        "flags. Authentication is OPTIONAL — guests receive the same "
        "prediction but are not associated with the resulting symptom log."
    ),
)
async def predict_from_text(
    payload: SymptomTextRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> PredictionResponse:
    return await prediction_service.predict_from_text(db, payload, current_user)
