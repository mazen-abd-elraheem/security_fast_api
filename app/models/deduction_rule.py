"""
SecureTrack Platform — Deduction Rule Model
Configurable salary deduction rules managed by admin.
"""
from sqlalchemy import Column, String, Float, Boolean, DateTime, Integer
from datetime import datetime, timezone

from app.core.database import Base


class DeductionRule(Base):
    __tablename__ = "deduction_rules"

    rule_id = Column(String(36), primary_key=True, index=True)

    # Rule identification
    rule_type = Column(String(50), nullable=False, unique=True)  # absent, late, early_leave, no_checkout, overtime_bonus
    label = Column(String(100), nullable=False)  # Display name
    description = Column(String(500), nullable=True)

    # Deduction/bonus config
    amount = Column(Float, nullable=False, default=0.0)  # Fixed amount in EGP
    is_per_minute = Column(Boolean, nullable=False, default=False)  # If true, amount is per-minute
    threshold_minutes = Column(Integer, nullable=False, default=0)  # Grace period before rule triggers

    # Whether this rule is active
    is_active = Column(Boolean, nullable=False, default=True)

    # Whether this is a deduction (negative) or bonus (positive)
    is_bonus = Column(Boolean, nullable=False, default=False)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<DeductionRule(type={self.rule_type}, amount={self.amount}, active={self.is_active})>"
