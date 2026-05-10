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
    is_admin: bool = False
    is_super_admin: bool = False
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
class ChatTurn(BaseModel):
    """
    One turn in the back-and-forth between the patient and the assistant.
    The ``user`` text is PII-redacted (``[REDACTED]`` placeholders); the
    ``assistant`` text is either the chosen follow-up question or the
    closing acknowledgement before the final assessment.
    """

    role: Literal["user", "assistant"]
    text: str


class ConfidenceStatus(BaseModel):
    """
    Live snapshot of the threshold gate driving the chat flow.

    * ``current_topk`` — top-1 calibrated probability the classifier produced
      on the combined user text *for this turn*.
    * ``required_topk`` — the configured ``PREDICTION_CONFIDENCE_THRESHOLD``;
      once ``current_topk`` reaches it, the assistant finalises instead of
      asking another follow-up.
    """

    current_topk: float = Field(ge=0.0, le=1.0)
    required_topk: float = Field(ge=0.0, le=1.0)


class PredictionResponse(BaseModel):
    """
    Response for POST /predict/text.

    The endpoint serves two flows on the same shape:

    * ``requires_followup=True`` — the model isn't confident yet. The
      assistant's question is in ``followup_message`` and ``log_id`` keeps
      the conversation pinned to a single DB row. Triage / explanation /
      enrichment fields are filler-only so the client can render the chat
      without branching on shape.
    * ``requires_followup=False`` — final assessment. ``top_conditions``,
      ``triage_level`` and ``explanation`` are real and usable.

    Top-level enrichment fields (`recommended_specialist`, `care_tips`,
    `red_flags`) duplicate the primary condition so simple clients only need
    to read the root of the response.
    """

    log_id: int | None = None
    language: LanguageLiteral = "English"

    # Populated by GET /history/{log_id} so the detail screen can show the
    # original input + when the consultation was saved without a second call.
    # Always None on the live POST /predict/text response (the client already
    # has the raw text in hand and there's no created_at yet).
    raw_text: str | None = None
    created_at: datetime | None = None

    # Stateful chat flow.
    requires_followup: bool = False
    followup_message: str | None = None
    chat_history: list[ChatTurn] = Field(default_factory=list)
    confidence_status: ConfidenceStatus | None = None

    top_conditions: list[TopCondition] = Field(default_factory=list)

    triage_level: TriageLevel | None = None
    recommended_specialist: str | None = None
    care_tips: CareTipsBilingual = Field(default_factory=CareTipsBilingual)
    red_flags: list[str] = Field(default_factory=list)

    extracted_symptoms: list[str] = Field(default_factory=list)
    explanation: Explanation | None = None
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
