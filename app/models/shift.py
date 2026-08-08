"""
SecureTrack Platform — Shift Model
Time blocks defining when guards are needed at a site.
"""
from sqlalchemy import Column, String, Integer, Time, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        Index('ix_shifts_site_active', 'site_id', 'is_active'),
    )

    shift_id = Column(String(36), primary_key=True, index=True)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False, index=True)

    # Shift time window
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Days of week when this shift is active (comma-separated: "mon,tue,wed,thu,fri")
    days_of_week = Column(String(100), nullable=False, default="mon,tue,wed,thu,fri,sat,sun")

    # How many guards are required for this shift
    required_headcount = Column(Integer, nullable=False, default=1)

    # Shift label (e.g., "Morning Shift", "Night Watch")
    label = Column(String(100), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    site = relationship("Site", back_populates="shifts")
    guard_rosters = relationship("GuardRoster", back_populates="shift", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Shift(shift_id={self.shift_id}, site={self.site_id}, {self.start_time}-{self.end_time})>"
