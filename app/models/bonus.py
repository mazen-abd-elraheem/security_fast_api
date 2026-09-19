"""
SecureTrack Platform — Bonus Model
Tracks bonus requests and records for guards.
"""
from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Index
from datetime import datetime, timezone

from app.core.database import Base


class Bonus(Base):
    __tablename__ = "bonuses"
    __table_args__ = (
        Index('ix_bonus_guard', 'guard_id'),
        Index('ix_bonus_status', 'status'),
    )

    bonus_id = Column(String(36), primary_key=True, index=True)

    # Who is the bonus for
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)  # Denormalized for display
    guard_code = Column(String(50), nullable=True)  # badge_number of the guard

    # Context (Where, when, who was supervising)
    site_id = Column(String(36), nullable=True)
    site_name = Column(String(255), nullable=True)
    supervisor_name = Column(String(255), nullable=True)
    shift_time = Column(String(50), nullable=True)

    # Bonus details
    amount = Column(Float, nullable=False, default=0.0)
    photo_url = Column(String(1024), nullable=True)  # Proof / approval doc
    status = Column(String(30), nullable=False, default="pending")  # pending, approved, rejected
    notes = Column(Text, nullable=True)

    # Timestamps & Tracking
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(36), nullable=True)
