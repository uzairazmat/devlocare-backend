"""
Response schemas returned by API endpoints.

This file owns the canonical Pydantic shapes returned by the v1 API and the
medical-disclaimer constants. The prediction-related models implement the
contract negotiated for UC-01 / UC-03 / UC-04 / UC-05:

    - TriageLevel: one of {self_care, see_gp, urgent_care} (matches `disease_kb.triage_category`)
    - Bilingual CareTips object that always carries both English and Urdu
    - Per-condition KB enrichment in `TopCondition` (specialist, care tips, red flags, triage)
    - Structured Explanation block (rationale + key symptoms + confidence breakdown)
    - Language-aware disclaimer
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# --------------------------------------------------------------------------- #
# Auth / User                                                                  #
# --------------------------------------------------------------------------- #
class UserResponse(BaseModel):
    """Public user profile — never exposes password_hash."""

    user_id: int
    username: str
    email: EmailStr
    language_pref: Literal["English", "Urdu"]
    age: int | None = None
    sex: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    """Simple message envelope used for generic success responses."""

    message: str


# --------------------------------------------------------------------------- #
# Symptom Prediction (UC-01 + UC-03 + UC-04 + UC-05)                           #
# --------------------------------------------------------------------------- #
LanguageLiteral = Literal["English", "Urdu"]
TriageLevel = Literal["self_care", "see_gp", "urgent_care"]


class CareTipsBilingual(BaseModel):
    """
    Care tips returned in BOTH languages so the client can switch UI language
    without a second API call. Either side may be `None` when the KB does not
    have a translation.
    """

    en: str | None = None
    ur: str | None = None


class TopCondition(BaseModel):
    """
    One entry in the Top-K prediction list, fully enriched from the KB.

    `name` is already localized to the caller's language preference; `name_en`
    keeps the canonical English name so the client (and analytics) can
    correlate predictions across languages without re-resolving.
    """

    name: str
    name_en: str
    confidence: float = Field(ge=0.0, le=1.0)
    specialist_type: str | None = None
    triage_category: TriageLevel | None = None
    care_tips: CareTipsBilingual = Field(default_factory=CareTipsBilingual)
    red_flags: list[str] = Field(default_factory=list)


class FeatureImportance(BaseModel):
    """
    One bar in the UC-04 feature-importance chart.

    The frontend (Plotly.js / Chart.js) renders the array as a horizontal bar
    chart, so `importance` is normalised to roughly [0, 1] (relative weight
    against the largest contributor for the prediction).
    """

    feature: str
    importance: float = Field(ge=0.0, le=1.0)


class Explanation(BaseModel):
    """
    UC-04 explanation block.

    - `rationale`: a short, natural-language sentence explaining why the model
      chose the top condition.
    - `key_symptoms`: the most important symptoms / phrases that influenced
      the prediction.
    - `feature_importance`: ranked list of features driving the top class —
      ready to feed into a Plotly.js / Chart.js bar chart.
    - `confidence_breakdown`: extended sentence kept for backwards
      compatibility with existing clients.
    """

    rationale: str
    key_symptoms: list[str]
    feature_importance: list[FeatureImportance] = Field(default_factory=list)
    confidence_breakdown: str


# --------------------------------------------------------------------------- #
# Disclaimer constants                                                         #
# --------------------------------------------------------------------------- #
MEDICAL_DISCLAIMER_EN: str = (
    "This tool provides general health information and is NOT a substitute "
    "for professional medical advice, diagnosis or treatment. Always consult "
    "a qualified healthcare provider. If you believe you have a medical "
    "emergency, call your local emergency services immediately."
)
MEDICAL_DISCLAIMER_UR: str = (
    "یہ ٹول صرف عمومی صحت کی معلومات فراہم کرتا ہے اور پیشہ ورانہ طبی مشورے، "
    "تشخیص یا علاج کا متبادل نہیں ہے۔ ہمیشہ کسی مستند معالج سے مشورہ کریں۔ "
    "اگر آپ کو طبی ایمرجنسی محسوس ہو تو فوری طور پر ہنگامی خدمات سے رابطہ کریں۔"
)


def get_disclaimer(language: LanguageLiteral) -> str:
    """Return the appropriate disclaimer for the given language preference."""
    return MEDICAL_DISCLAIMER_UR if language == "Urdu" else MEDICAL_DISCLAIMER_EN


# Backwards-compat re-export — older code (and tests) may still import this.
MEDICAL_DISCLAIMER = MEDICAL_DISCLAIMER_EN


# --------------------------------------------------------------------------- #
# Top-level prediction response                                                #
# --------------------------------------------------------------------------- #
class PredictionResponse(BaseModel):
    """
    Final response for POST /predict/text.

    The top-level fields (`recommended_specialist`, `care_tips`, `red_flags`)
    duplicate the primary condition's enrichment so simple clients only need
    to read the root of the response. Multi-condition clients should iterate
    `top_conditions` instead — every entry is independently enriched.
    """

    log_id: int | None = None
    language: LanguageLiteral = "English"

    top_conditions: list[TopCondition]

    triage_level: TriageLevel
    recommended_specialist: str | None = None
    care_tips: CareTipsBilingual = Field(default_factory=CareTipsBilingual)
    red_flags: list[str] = Field(default_factory=list)

    extracted_symptoms: list[str] = Field(default_factory=list)
    explanation: Explanation
    disclaimer: str = MEDICAL_DISCLAIMER_EN


# --------------------------------------------------------------------------- #
# History (UC-06)                                                              #
# --------------------------------------------------------------------------- #
class HistoryItem(BaseModel):
    """
    Compact row used by the ChatGPT-style sidebar consultation list.

    Heavy fields (`explanation_json`, full top-K predictions, feature
    importance) are intentionally NOT included — the sidebar only needs
    enough to render a clickable preview. Detailed payloads come from the
    PDF export endpoint or a future `GET /history/{log_id}` detail endpoint.
    """

    log_id: int
    created_at: datetime
    raw_text: str = Field(
        description="Shortened preview (max ~140 chars) of the original input.",
    )
    main_condition: str | None = None
    triage_level: TriageLevel | None = None

    model_config = ConfigDict(from_attributes=True)


class HistoryResponse(BaseModel):
    """Paginated envelope for GET /history."""

    items: list[HistoryItem] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    limit: int
    offset: int
    message: str | None = None


# --------------------------------------------------------------------------- #
# Consultation feedback (UC-08)                                                #
# --------------------------------------------------------------------------- #
class FeedbackResponse(BaseModel):
    """
    Stored feedback row returned by ``POST /api/v1/feedback``.

    The endpoint also emits a ``message`` envelope around this object — see
    :class:`FeedbackSubmitResponse` — so the frontend can display a toast
    while still having structured data to update local state with.
    """

    feedback_id: int
    log_id: int
    user_id: int
    helpfulness_rating: int = Field(ge=1, le=5)
    feedback_text: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class FeedbackSubmitResponse(BaseModel):
    """Envelope returned by the submit-feedback endpoint."""

    message: str
    feedback: FeedbackResponse


class _RecentLowRatedCase(BaseModel):
    """One row in the ``recent_low_rated_cases`` monitoring list."""

    feedback_id: int
    log_id: int
    helpfulness_rating: int
    feedback_text: str | None = None
    main_condition: str | None = None
    triage_level: TriageLevel | None = None
    created_at: datetime


class _NegativeComment(BaseModel):
    """One row in the ``top_negative_comments`` monitoring list."""

    feedback_id: int
    log_id: int
    helpfulness_rating: int
    feedback_text: str
    created_at: datetime


class _FeedbackByTriage(BaseModel):
    """Aggregated feedback bucketed by triage level."""

    triage_level: TriageLevel | None = None
    count: int
    average_rating: float


class _FeedbackByCondition(BaseModel):
    """Aggregated feedback bucketed by predicted condition."""

    main_condition: str | None = None
    count: int
    average_rating: float


class FeedbackMonitorResponse(BaseModel):
    """
    Lightweight aggregate payload for ``GET /api/v1/feedback/monitor``.

    Designed to be the only call a model-monitoring dashboard needs to
    detect: (a) whether users are unhappy, (b) which consultations recently
    earned low ratings, (c) which predicted conditions get criticised, and
    (d) what users actually say in their comments.
    """

    total_feedback: int
    average_rating: float
    rating_distribution: dict[str, int]
    low_rating_count: int
    recent_low_rated_cases: list[_RecentLowRatedCase] = Field(default_factory=list)
    top_negative_comments: list[_NegativeComment] = Field(default_factory=list)
    feedback_by_triage: list[_FeedbackByTriage] = Field(default_factory=list)
    feedback_by_main_condition: list[_FeedbackByCondition] = Field(
        default_factory=list,
    )
