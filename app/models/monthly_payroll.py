"""
SecureTrack Platform — Monthly Payroll Model
Stores monthly payroll snapshots per employee. Generated from leader attendance records.
"""
from sqlalchemy import Column, String, Float, DateTime, Integer, Boolean, Text, Index
from datetime import datetime, timezone

from app.core.database import Base


class MonthlyPayroll(Base):
    __tablename__ = "monthly_payroll"
    __table_args__ = (
        Index('ix_payroll_year_month', 'year', 'month'),
        Index('ix_payroll_user_period', 'user_id', 'year', 'month', unique=True),
    )

    payroll_id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)

    # Employee info snapshot
    employee_name = Column(String(255), nullable=False)
    badge_number = Column(String(50), nullable=True)
    classification = Column(String(50), nullable=True)
    role = Column(String(30), nullable=True)

    # Attendance summary (from leader records)
    days_present = Column(Integer, nullable=False, default=0)
    days_absent = Column(Integer, nullable=False, default=0)
    days_late = Column(Integer, nullable=False, default=0)
    days_leave = Column(Integer, nullable=False, default=0)  # Approved leaves
    total_scheduled_days = Column(Integer, nullable=False, default=0)
    working_days = Column(Float, nullable=False, default=0.0)  # Effective working days

    # Salary calculation
    daily_rate = Column(Float, nullable=False, default=0.0)
    base_salary = Column(Float, nullable=False, default=0.0)
    gross_salary = Column(Float, nullable=False, default=0.0)  # working_days × daily_rate

    # Deductions
    absence_deduction = Column(Float, nullable=False, default=0.0)
    late_deduction = Column(Float, nullable=False, default=0.0)
    advance_deduction = Column(Float, nullable=False, default=0.0)  # Cash advance repayment
    insurance_deduction = Column(Float, nullable=False, default=0.0)
    tax_deduction = Column(Float, nullable=False, default=0.0)
    other_deductions = Column(Float, nullable=False, default=0.0)
    total_deductions = Column(Float, nullable=False, default=0.0)

    # Additions
    incentive = Column(Float, nullable=False, default=0.0)
    bonus = Column(Float, nullable=False, default=0.0)
    overtime_pay = Column(Float, nullable=False, default=0.0)
    total_additions = Column(Float, nullable=False, default=0.0)

    # Final
    net_salary = Column(Float, nullable=False, default=0.0)

    # Status
    is_finalized = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)

    # Timestamps
    generated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    finalized_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<MonthlyPayroll(user={self.employee_name}, {self.year}/{self.month}, net={self.net_salary})>"
