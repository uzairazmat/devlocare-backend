"""
Response schemas returned by API endpoints.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr


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
