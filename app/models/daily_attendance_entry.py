"""
SecureTrack - Daily Attendance Entry Model
Records one entry per employee per day, entered by a Leader.
"""
import uuid
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, Date, Text, ForeignKey, Index, UniqueConstraint
from datetime import datetime, timezone
from app.core.database import Base


class DailyAttendanceEntry(Base):
    __tablename__ = "daily_attendance_entries"
    __table_args__ = (
        UniqueConstraint("employee_id", "entry_date", name="uq_employee_date"),
        Index("ix_dae_site_date", "site_id", "entry_date"),
        Index("ix_dae_employee_date", "employee_id", "entry_date"),
        Index("ix_dae_entered_by", "entered_by"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    entry_date = Column(Date, nullable=False)

    # present / absence_excused / absence_unexcused / annual_leave / sick_leave / rest / rest_day_worked
    status = Column(String(30), nullable=False, default="present")

    late_minutes = Column(Integer, nullable=False, default=0)
    overtime_hours = Column(Float, nullable=False, default=0.0)
    overtime_approved = Column(Boolean, nullable=False, default=False)
    overtime_approved_by = Column(String(36), nullable=True)
    excused_by = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)

    entered_by = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)

    locked = Column(Boolean, nullable=False, default=False)
    locked_at = Column(DateTime, nullable=True)
    override_reason = Column(Text, nullable=True)
    overridden_by = Column(String(36), nullable=True)
    overridden_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<DailyAttendanceEntry(id={self.id}, emp={self.employee_id}, date={self.entry_date})>"
