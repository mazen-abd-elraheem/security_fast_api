"""
SecureTrack Platform — Employee Rest Allowance Model
Stores custom/extra rest allowances assigned to specific employees, overriding or supplementing their role default.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base


class EmployeeRestAllowance(Base):
    __tablename__ = "employee_rest_allowance"

    assignment_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    # Which user is this for
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    # Admin who assigned it
    assigned_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)

    # Extra allowance amount (money or days)
    value = Column(Float, nullable=False, default=0.0)

    # If true, `value` is number of days. If false, it's a fixed EGP amount.
    is_days_multiplier = Column(Boolean, nullable=False, default=True)

    # The month this applies to (e.g. "2026-09"). If null, it applies permanently.
    month_year = Column(String(7), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    admin = relationship("User", foreign_keys=[assigned_by])

    def __repr__(self):
        return f"<EmployeeRestAllowance(user_id={self.user_id}, value={self.value}, is_days={self.is_days_multiplier})>"
