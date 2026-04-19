"""
Request schemas accepted by API endpoints.
All validation is enforced by Pydantic v2.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.response import UserResponse


LanguagePref = Literal["English", "Urdu"]
SexLiteral = Literal["Male", "Female", "Other"]


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
