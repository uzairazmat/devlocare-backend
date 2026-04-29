"""
Request schemas accepted by API endpoints.
All validation is enforced by Pydantic v2.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.response import UserResponse


LanguagePref = Literal["English", "Urdu"]
SexLiteral = Literal["Male", "Female", "Other"]
SeverityLiteral = Literal["mild", "moderate", "severe"]


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
class UserRegisterRequest(BaseModel):
    """Payload for POST /auth/register."""

    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    age: int | None = Field(default=None, ge=0, le=120)
    sex: SexLiteral | None = None
    language_pref: LanguagePref = "English"

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("username")
    @classmethod
    def _username_chars(cls, v: str) -> str:
        if not all(c.isalnum() or c in {"_", "-", "."} for c in v):
            raise ValueError(
                "username may contain only letters, digits, '_', '-', '.'"
            )
        return v


class UserLoginRequest(BaseModel):
    """Payload for POST /auth/login. Accepts either username or email."""

    username_or_email: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(str_strip_whitespace=True)


class UserUpdateRequest(BaseModel):
    """Payload for PUT /auth/me. All fields optional."""

    age: int | None = Field(default=None, ge=0, le=120)
    sex: SexLiteral | None = None
    language_pref: LanguagePref | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Token response (grouped with auth payloads for convenience)                  #
# --------------------------------------------------------------------------- #
class TokenResponse(BaseModel):
    """JWT bundle returned on successful login or registration."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


# --------------------------------------------------------------------------- #
# Symptom Prediction (UC-01)                                                   #
# --------------------------------------------------------------------------- #

# PII guard: e-mail, any digit-run >= 7 (phones, CNIC, card numbers),
# Pakistani CNIC (#####-#######-#), 16-digit card groupings.
_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                  # email
    re.compile(r"\b\d{5}-\d{7}-\d\b"),                         # CNIC
    re.compile(r"(?:\d[\s-]?){13,19}"),                        # card / long nums
    re.compile(r"\+?\d[\d\s\-()]{6,}\d"),                      # phone
)


class SymptomTextRequest(BaseModel):
    """
    Payload for POST /predict/text.
    `text` is a free-form symptom description. Metadata is optional and is
    used for personalization/triage (pregnancy, age, chronic disease etc.).
    """

    text: str = Field(min_length=5, max_length=500)
    age: int | None = Field(default=None, ge=0, le=120)
    sex: SexLiteral | None = None
    duration: str | None = Field(
        default=None,
        max_length=50,
        description="Free-form duration, e.g. '3 days', '2 weeks'.",
    )
    severity: SeverityLiteral | None = None
    pregnancy: bool = Field(
        default=False,
        description="Set to true only if the patient is currently pregnant.",
    )
    chronic_disease: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional — comma-separated chronic conditions "
            "(e.g. 'diabetes, hypertension'). Leave null/empty if none."
        ),
    )

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("text")
    @classmethod
    def _block_pii(cls, v: str) -> str:
        """Reject input that appears to contain PII."""
        for pattern in _PII_PATTERNS:
            if pattern.search(v):
                raise ValueError(
                    "Personal information (email, phone, CNIC or card number) "
                    "is not allowed in symptom text."
                )
        return v


# --------------------------------------------------------------------------- #
# History export (UC-06)                                                       #
# --------------------------------------------------------------------------- #
class HistoryExportRequest(BaseModel):
    """
    Payload for POST /history/export.

    The caller picks ONE of two selection modes:

    * ``log_ids`` — explicit list of log_id values the user has selected in the
      sidebar (max 50 to keep the PDF reasonable).
    * ``last_n``  — the most recent N consultations (1-50). Convenient for a
      "Export recent history" button.

    Exactly one of the two fields must be provided. Records that don't belong
    to the authenticated user are silently filtered out at the repository
    layer — no information leakage.
    """

    log_ids: list[int] | None = Field(
        default=None,
        description="Explicit list of log_id values to export.",
    )
    last_n: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Export the N most recent consultations (1-50).",
    )

    @field_validator("log_ids")
    @classmethod
    def _validate_log_ids(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("log_ids must contain at least one id")
        if len(v) > 50:
            raise ValueError("log_ids cannot contain more than 50 entries")
        if any(i <= 0 for i in v):
            raise ValueError("log_ids must be positive integers")
        return list(dict.fromkeys(v))

    def resolved_mode(self) -> Literal["log_ids", "last_n"]:
        if self.log_ids and self.last_n:
            raise ValueError("Provide either log_ids OR last_n, not both")
        if self.log_ids:
            return "log_ids"
        if self.last_n:
            return "last_n"
        raise ValueError("Provide log_ids or last_n")


# --------------------------------------------------------------------------- #
# Consultation feedback (UC-08)                                                #
# --------------------------------------------------------------------------- #
FEEDBACK_TEXT_MAX_LENGTH: int = 1000


class FeedbackCreateRequest(BaseModel):
    """
    Payload for ``POST /api/v1/feedback``.

    Fields
    ------
    log_id
        Identifier of the consultation (``symptom_logs.log_id``) the user is
        rating. Ownership is enforced server-side — supplying somebody
        else's ``log_id`` is rejected.
    helpfulness_rating
        Strict integer 1-5. Anything outside that range is rejected before
        it ever reaches the DB.
    feedback_text
        Optional free-form comment. Whitespace is stripped and the value is
        capped at ``FEEDBACK_TEXT_MAX_LENGTH`` characters; an empty / blank
        string is normalised to ``None`` so we don't store noise.
    """

    log_id: int = Field(gt=0, description="Target consultation log_id.")
    helpfulness_rating: int = Field(
        ge=1, le=5,
        description="How helpful the consultation was (1=worst, 5=best).",
    )
    feedback_text: str | None = Field(
        default=None,
        max_length=FEEDBACK_TEXT_MAX_LENGTH,
        description="Optional free-text comment (≤ 1000 chars).",
    )

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("feedback_text")
    @classmethod
    def _normalise_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        # Hard-cap as a defence-in-depth guard even though Field also enforces.
        if len(cleaned) > FEEDBACK_TEXT_MAX_LENGTH:
            cleaned = cleaned[:FEEDBACK_TEXT_MAX_LENGTH]
        return cleaned
