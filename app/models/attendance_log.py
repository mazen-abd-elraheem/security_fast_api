"""
SecureTrack Platform — Attendance Log Model
Records the actual presence state of a guard as reported by a supervisor during a visit.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"
    __table_args__ = (
        Index('ix_attendance_roster_visit', 'roster_id', 'visit_id'),
        Index('ix_attendance_supervisor_date', 'supervisor_id', 'recorded_at'),
        Index('ix_attendance_status_date', 'status', 'recorded_at'),
    )

    log_id = Column(String(36), primary_key=True, index=True)
    roster_id = Column(String(36), ForeignKey("guard_roster.roster_id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(String(36), ForeignKey("supervisor_visits.visit_id", ondelete="CASCADE"), nullable=True, index=True)
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    # Attendance status: present, absent, late, replacement
    status = Column(String(20), nullable=False)

    # If status is "replacement", who replaced the original guard
    replacement_guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)

    # Optional notes (e.g., "Guard arrived 15 min late due to traffic")
    notes = Column(Text, nullable=True)

    # When this record was created
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # When the outdoor user checked out (NULL if still checked in or not applicable)
    checkout_at = Column(DateTime, nullable=True)

    # Relationships
    roster = relationship("GuardRoster", back_populates="attendance_logs")
    visit = relationship("SupervisorVisit", back_populates="attendance_logs")
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    replacement_guard = relationship("User", foreign_keys=[replacement_guard_id])

    def __repr__(self):
        return f"<AttendanceLog(log_id={self.log_id}, status={self.status}, roster={self.roster_id})>"
