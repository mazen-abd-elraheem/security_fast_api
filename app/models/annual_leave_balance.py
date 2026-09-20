"""
SecureTrack Platform — Annual Leave Balance Model
Tracks the annual leave entitlement and usage per employee per year.
"""
import uuid
from sqlalchemy import Column, String, Integer, Date, DateTime, ForeignKey, UniqueConstraint, Index
from datetime import datetime, timezone
from app.core.database import Base


class AnnualLeaveBalance(Base):
    __tablename__ = "annual_leave_balances"
    __table_args__ = (
        UniqueConstraint("employee_id", "year", name="uq_annual_leave_emp_year"),
        Index("ix_alb_employee_year", "employee_id", "year"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    year = Column(Integer, nullable=False)
    total_days = Column(Integer, nullable=False, default=21)
    used_days = Column(Integer, nullable=False, default=0)
    # Date from which employee is eligible (hire_date + 3 months)
    eligible_from = Column(Date, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AnnualLeaveBalance(emp={self.employee_id}, year={self.year}, used={self.used_days}/{self.total_days})>"
