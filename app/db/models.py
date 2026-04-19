from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime,
    ForeignKey, CheckConstraint, Index,
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
    feedback = relationship("Feedback", back_populates="log")


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


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        Index("idx_log_id", "log_id"),
    )

    feedback_id = Column(Integer, primary_key=True, autoincrement=True)
    log_id = Column(
        Integer,
        ForeignKey("symptom_logs.log_id", ondelete="CASCADE"),
        nullable=True,
    )
    rating = Column(Integer, CheckConstraint("rating >= 1 AND rating <= 5"))
    comments = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    log = relationship("SymptomLog", back_populates="feedback")
