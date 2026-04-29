from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime,
    ForeignKey, CheckConstraint, Index, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_username", "username"),
        Index("idx_email", "email"),
    )

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    language_pref = Column(String(10), default="English")
    age = Column(Integer)
    sex = Column(String(10))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    logs = relationship("SymptomLog", back_populates="user")


class SymptomLog(Base):
    __tablename__ = "symptom_logs"
    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_created_at", "created_at"),
    )

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_text = Column(Text, nullable=False)
    transcription = Column(Text)
    predicted_condition = Column(String(100))
    confidence_score = Column(Float)
    explanation_json = Column(Text)
    triage_level = Column(String(20))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="logs")
    feedback = relationship(
        "ConsultationFeedback",
        back_populates="log",
        cascade="all, delete-orphan",
    )


class DiseaseKB(Base):
    __tablename__ = "disease_kb"
    __table_args__ = (
        Index("idx_name_en", "name_en"),
    )

    disease_id = Column(Integer, primary_key=True, autoincrement=True)
    name_en = Column(String(100), nullable=False)
    name_ur = Column(String(100))
    specialist_type = Column(String(50))
    triage_category = Column(String(20))
    care_tips_en = Column(Text)
    care_tips_ur = Column(Text)
    red_flags = Column(Text)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ConsultationFeedback(Base):
    """
    UC-08 — Consultation feedback table.

    Stores the helpfulness rating (1-5) and optional free-text comment a
    registered user submits after viewing a consultation result. Owns its own
    table (``consultation_feedback``) so ``symptom_logs`` is never polluted
    with feedback columns. One feedback row per (log_id, user_id) pair —
    re-submissions update the existing row instead of duplicating.
    """

    __tablename__ = "consultation_feedback"
    __table_args__ = (
        UniqueConstraint(
            "log_id", "user_id",
            name="uq_consultation_feedback_log_user",
        ),
        Index("idx_consultation_feedback_log_id", "log_id"),
        Index("idx_consultation_feedback_user_id", "user_id"),
        Index("idx_consultation_feedback_rating", "helpfulness_rating"),
        CheckConstraint(
            "helpfulness_rating >= 1 AND helpfulness_rating <= 5",
            name="ck_consultation_feedback_rating_range",
        ),
    )

    feedback_id = Column(Integer, primary_key=True, autoincrement=True)
    log_id = Column(
        Integer,
        ForeignKey("symptom_logs.log_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    helpfulness_rating = Column(Integer, nullable=False)
    feedback_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    log = relationship("SymptomLog", back_populates="feedback")
