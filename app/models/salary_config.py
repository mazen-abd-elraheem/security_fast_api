"""
SecureTrack Platform — Salary Configuration Model
Maps classification (نائب/مشرف/فرد) to daily rates, insurance, and incentives.
"""
from sqlalchemy import Column, String, Float, Boolean, DateTime, Integer
from datetime import datetime, timezone

from app.core.database import Base


class SalaryConfig(Base):
    __tablename__ = "salary_configs"

    config_id = Column(String(36), primary_key=True, index=True)
    classification = Column(String(50), nullable=False, unique=True, index=True)  # e.g., "فرد", "نائب", "مشرف"
    label_en = Column(String(100), nullable=True)  # e.g., "Guard", "Deputy", "Supervisor"
    daily_rate = Column(Float, nullable=False, default=0.0)  # EGP per working day
    monthly_base = Column(Float, nullable=False, default=0.0)  # Base monthly salary
    insurance_employee_share = Column(Float, nullable=False, default=0.0)  # Monthly insurance deduction
    incentive_rate = Column(Float, nullable=False, default=0.0)  # Monthly incentive amount
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<SalaryConfig(classification={self.classification}, daily_rate={self.daily_rate})>"
