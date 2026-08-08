"""
SecureTrack Platform — Guard Roster Model
Maps guards to specific shifts on specific dates.
"""
from sqlalchemy import Column, String, Date, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class GuardRoster(Base):
    __tablename__ = "guard_roster"
    __table_args__ = (
        Index('ix_roster_guard_date', 'guard_id', 'assigned_date'),
        Index('ix_roster_shift_date', 'shift_id', 'assigned_date', 'status'),
    )

    roster_id = Column(String(36), primary_key=True, index=True)
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    shift_id = Column(String(36), ForeignKey("shifts.shift_id", ondelete="CASCADE"), nullable=False, index=True)

    # The specific date this assignment is for
    assigned_date = Column(Date, nullable=False, index=True)

    # Status: scheduled, active, canceled
    status = Column(String(20), nullable=False, default="scheduled")

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", back_populates="guard_rosters", foreign_keys=[guard_id])
    shift = relationship("Shift", back_populates="guard_rosters")
    attendance_logs = relationship("AttendanceLog", back_populates="roster", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<GuardRoster(roster_id={self.roster_id}, guard={self.guard_id}, date={self.assigned_date})>"
