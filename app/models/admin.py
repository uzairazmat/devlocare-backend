"""
Pydantic schemas dedicated to the Admin Panel API.

Kept separate from ``request.py`` / ``response.py`` so the patient-facing
contract stays small and the admin payloads can evolve independently.

Modules using these schemas:
    * ``app/api/v1/endpoints/admin/dashboard.py``
    * ``app/api/v1/endpoints/admin/consultations.py``
    * ``app/api/v1/endpoints/admin/feedback.py``
    * ``app/api/v1/endpoints/admin/knowledge_base.py``
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.response import LanguageLiteral, TriageLevel


# --------------------------------------------------------------------------- #
# 1. DASHBOARD                                                                 #
# --------------------------------------------------------------------------- #
class TopDiseaseStat(BaseModel):
    """One row in the 'most predicted diseases' bar chart."""

    main_condition: str
    count: int
    avg_confidence: float = Field(ge=0.0, le=1.0)


class TriageDistributionStat(BaseModel):
    """One slice of the triage distribution donut chart."""

    triage_level: TriageLevel | Literal["unknown"]
    count: int
    percentage: float = Field(ge=0.0, le=100.0)


class LanguageUsageStat(BaseModel):
    """One slice of the language-usage donut chart."""

    language: LanguageLiteral | Literal["Unknown"]
    count: int
    percentage: float = Field(ge=0.0, le=100.0)


class DashboardCounters(BaseModel):
    """Headline KPI cards on the admin dashboard."""

    total_consultations: int
    today_consultations: int
    urgent_cases: int
    average_feedback_rating: float = Field(ge=0.0, le=5.0)
    total_feedback: int


class DashboardResponse(BaseModel):
    """
    Single payload powering the entire dashboard screen.

    Built so the frontend can render every chart from one HTTP call. If a
    chart is added later, append a new field here — the existing fields stay
    backwards compatible.
    """

    counters: DashboardCounters
    top_diseases: list[TopDiseaseStat] = Field(default_factory=list)
    triage_distribution: list[TriageDistributionStat] = Field(default_factory=list)
    language_usage: list[LanguageUsageStat] = Field(default_factory=list)
    generated_at: datetime


# --------------------------------------------------------------------------- #
# 2. CONSULTATION MONITOR                                                      #
# --------------------------------------------------------------------------- #
class AdminConsultationListItem(BaseModel):
    """Compact row used by the admin consultations table."""

    log_id: int
    user_id: int | None = None
    username: str | None = None
    main_condition: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    # Stored as plain str (instead of strict Literal) so legacy / dirty
    # rows (e.g. "moderate") still surface to the admin instead of 500'ing
    # validation. The dashboard donut chart buckets them into "unknown".
    triage_level: str | None = None
    language: str | None = None
    raw_text_preview: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminConsultationListResponse(BaseModel):
    """Paginated envelope for ``GET /admin/consultations``."""

    items: list[AdminConsultationListItem] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    limit: int
    offset: int
    filters: dict[str, str | None] = Field(default_factory=dict)


class AdminTopPrediction(BaseModel):
    """One of the Top-3 conditions stored in ``explanation_json``."""

    name_en: str
    confidence: float = Field(ge=0.0, le=1.0)
    specialist_type: str | None = None


class AdminShapSummary(BaseModel):
    """
    Compact view over the SHAP-style explainability bundle persisted at
    prediction time. Mirrors the fields used by UC-04.
    """

    rationale: str | None = None
    key_symptoms: list[str] = Field(default_factory=list)
    top_features: list[dict[str, float | str]] = Field(default_factory=list)
    confidence_breakdown: str | None = None


class AdminConsultationDetail(BaseModel):
    """
    Full consultation payload returned by ``GET /admin/consultations/{id}``.

    Heavy fields (top-3 predictions, SHAP, symptoms, triage decision,
    specialist) are surfaced as first-class properties so the admin UI does
    not need to parse ``explanation_json`` itself.
    """

    log_id: int
    user_id: int | None = None
    username: str | None = None
    raw_text: str
    language: str | None = None
    triage_level: str | None = None  # see AdminConsultationListItem note
    triage_rule: str | None = Field(
        default=None,
        description="Which triage rule fired (e.g. 'critical_keyword', 'kb_lookup').",
    )
    extracted_symptoms: list[str] = Field(default_factory=list)
    top_predictions: list[AdminTopPrediction] = Field(default_factory=list)
    recommended_specialist: str | None = None
    shap_summary: AdminShapSummary
    created_at: datetime
    feedback_rating: int | None = Field(default=None, ge=1, le=5)
    feedback_text: str | None = None


# --------------------------------------------------------------------------- #
# 3. FEEDBACK (admin view)                                                     #
# --------------------------------------------------------------------------- #
class FeedbackAdminSummary(BaseModel):
    """Headline cards on the admin feedback screen."""

    total_feedback: int
    average_rating: float = Field(ge=0.0, le=5.0)
    positive_percentage: float = Field(
        ge=0.0, le=100.0,
        description="% of feedback rows with rating >= 4.",
    )
    negative_percentage: float = Field(ge=0.0, le=100.0)
    rating_distribution: dict[str, int] = Field(default_factory=dict)


class FeedbackAdminListItem(BaseModel):
    """One row in the admin 'recent feedback' table."""

    feedback_id: int
    log_id: int
    user_id: int
    username: str | None = None
    helpfulness_rating: int = Field(ge=1, le=5)
    feedback_text: str | None = None
    main_condition: str | None = None
    triage_level: str | None = None
    created_at: datetime


class FeedbackAdminListResponse(BaseModel):
    """Paginated envelope for ``GET /admin/feedback``."""

    items: list[FeedbackAdminListItem] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# 4. KNOWLEDGE BASE (CRUD)                                                     #
# --------------------------------------------------------------------------- #
class DiseaseKBItem(BaseModel):
    """A row from ``disease_kb`` projected for the admin UI."""

    disease_id: int
    name_en: str
    name_ur: str | None = None
    specialist_type: str | None = None
    triage_category: TriageLevel | None = None
    care_tips_en: str | None = None
    care_tips_ur: str | None = None
    red_flags: str | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DiseaseKBListResponse(BaseModel):
    """Paginated envelope for ``GET /admin/diseases``."""

    items: list[DiseaseKBItem] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    limit: int
    offset: int


class DiseaseKBCreateRequest(BaseModel):
    """Payload for ``POST /admin/diseases``."""

    name_en: str = Field(min_length=2, max_length=100)
    name_ur: str | None = Field(default=None, max_length=100)
    specialist_type: str | None = Field(default=None, max_length=50)
    triage_category: TriageLevel | None = None
    care_tips_en: str | None = None
    care_tips_ur: str | None = None
    red_flags: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# 5. ADMIN USER MANAGEMENT (super-admin only)                                  #
# --------------------------------------------------------------------------- #
class CreateAdminRequest(BaseModel):
    """
    Payload for ``POST /admin/users/create-admin``.

    Mirrors :class:`UserRegisterRequest` shape but is intentionally a
    separate class so the patient-registration contract and the admin-
    provisioning contract can evolve independently. The created user is
    flagged ``is_admin=True``; super-admin status can never be granted via
    API — it is reserved for the startup bootstrap.
    """

    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("username")
    @classmethod
    def _username_chars(cls, v: str) -> str:
        if not all(c.isalnum() or c in {"_", "-", "."} for c in v):
            raise ValueError(
                "username may contain only letters, digits, '_', '-', '.'"
            )
        return v


class DiseaseKBUpdateRequest(BaseModel):
    """
    Payload for ``PUT /admin/diseases/{disease_id}``.

    All fields optional — only provided keys are written. ``None`` is treated
    as 'leave unchanged' rather than 'set to NULL' so accidental partial
    payloads cannot wipe data.
    """

    name_en: str | None = Field(default=None, min_length=2, max_length=100)
    name_ur: str | None = Field(default=None, max_length=100)
    specialist_type: str | None = Field(default=None, max_length=50)
    triage_category: TriageLevel | None = None
    care_tips_en: str | None = None
    care_tips_ur: str | None = None
    red_flags: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True)
