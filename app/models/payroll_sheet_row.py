"""
SecureTrack - Payroll Sheet Row Model
Stores every cell of the accountant's Excel-style payroll view permanently.
Each row = one employee for one month. All formulas are pre-computed on generation.
"""
import uuid
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, Text, Index
from datetime import datetime, timezone
from app.core.database import Base


class PayrollSheetRow(Base):
    __tablename__ = "payroll_sheet_rows"
    __table_args__ = (
        Index('ix_payroll_sheet_year_month', 'year', 'month'),
        Index('ix_payroll_sheet_user', 'user_id'),
    )

    row_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    user_id = Column(String(36), nullable=False)

    # Section 1: Employee Info (A-L)
    employee_code = Column(String(50))
    serial_no = Column(Integer)
    classification = Column(String(50))
    shift_time = Column(String(10))
    supervisor_name = Column(String(255))
    site_name = Column(String(255))
    hire_date = Column(String(20))
    termination_date = Column(String(20))
    uniform_status = Column(String(100))
    termination_reason = Column(String(255))
    insurance_status = Column(String(30))
    employee_name = Column(String(255))

    # Section 2: Weekly Attendance (4 weeks x 9 fields)
    w1_absent_excused = Column(Float, default=0)
    w1_absent_unexcused = Column(Float, default=0)
    w1_overtime = Column(Float, default=0)
    w1_rest_allowance = Column(Float, default=0)
    w1_late = Column(Float, default=0)
    w1_deduction = Column(Float, default=0)
    w1_rest = Column(Float, default=0)
    w1_annual_leave = Column(Float, default=0)
    w1_sick_leave = Column(Float, default=0)

    w2_absent_excused = Column(Float, default=0)
    w2_absent_unexcused = Column(Float, default=0)
    w2_overtime = Column(Float, default=0)
    w2_rest_allowance = Column(Float, default=0)
    w2_late = Column(Float, default=0)
    w2_deduction = Column(Float, default=0)
    w2_rest = Column(Float, default=0)
    w2_annual_leave = Column(Float, default=0)
    w2_sick_leave = Column(Float, default=0)

    w3_absent_excused = Column(Float, default=0)
    w3_absent_unexcused = Column(Float, default=0)
    w3_overtime = Column(Float, default=0)
    w3_rest_allowance = Column(Float, default=0)
    w3_late = Column(Float, default=0)
    w3_deduction = Column(Float, default=0)
    w3_rest = Column(Float, default=0)
    w3_annual_leave = Column(Float, default=0)
    w3_sick_leave = Column(Float, default=0)

    w4_absent_excused = Column(Float, default=0)
    w4_absent_unexcused = Column(Float, default=0)
    w4_overtime = Column(Float, default=0)
    w4_rest_allowance = Column(Float, default=0)
    w4_late = Column(Float, default=0)
    w4_deduction = Column(Float, default=0)
    w4_rest = Column(Float, default=0)
    w4_annual_leave = Column(Float, default=0)
    w4_sick_leave = Column(Float, default=0)

    # Section 3: Monthly Totals (AW-BF)
    total_work_days = Column(Float, default=30)
    total_absent_excused = Column(Float, default=0)
    total_absent_unexcused = Column(Float, default=0)
    total_overtime = Column(Float, default=0)
    total_rest_allowance = Column(Float, default=0)
    total_late = Column(Float, default=0)
    total_deduction = Column(Float, default=0)
    total_rest = Column(Float, default=0)
    total_annual_leave = Column(Float, default=0)
    total_sick_leave = Column(Float, default=0)

    # Section 4: Salary Calculation (BG-BQ)
    operational_days = Column(Float, default=0)
    daily_rate = Column(Float, default=0)
    salary_from_ops = Column(Float, default=0)
    annual_increase_current = Column(Float, default=0)
    annual_increase_prev = Column(Float, default=0)
    gross_salary = Column(Float, default=0)
    manual_deduction = Column(Float, default=0)
    advance_deduction = Column(Float, default=0)
    insurance_share = Column(Float, default=0)
    tax_deduction = Column(Float, default=0)
    net_salary = Column(Float, default=0)

    # Section 5: Bonus and Incentives (BR-CC)
    other_deductions = Column(Float, default=0)
    salary_diff = Column(Float, default=0)
    incentive = Column(Float, default=0)
    increase_2025 = Column(Float, default=0)
    bonus = Column(Float, default=0)
    bonus_deduction = Column(Float, default=0)
    total_incentive = Column(Float, default=0)
    payroll_amount = Column(Float, default=0)
    cash_payment = Column(Float, default=0)
    total_salary_diff_incentive = Column(Float, default=0)
    bonus_rounded = Column(Float, default=0)
    grand_incentive = Column(Float, default=0)

    # Section 6: Bank and Transfer (CD-CH)
    transfer_name = Column(String(255))
    incentive_transfer_name = Column(String(255))
    bank_account_1 = Column(String(100))
    bank_account_2 = Column(String(100))
    transfer_method = Column(String(100))

    # Section 7: Tax Calculation (CI-CR)
    monthly_salary = Column(Float, default=0)
    actual_salary = Column(Float, default=0)
    allowances = Column(Float, default=0)
    total_income = Column(Float, default=0)
    employee_insurance = Column(Float, default=0)
    annual_personal_exemption = Column(Float, default=0)
    net_after_insurance = Column(Float, default=0)
    annual_taxable = Column(Float, default=0)
    annual_tax = Column(Float, default=0)
    monthly_tax = Column(Float, default=0)

    # Meta
    is_approved = Column(Boolean, default=False)
    approved_by = Column(String(36), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    overridden_fields = Column(Text, nullable=True)


class SalaryClassificationConfig(Base):
    """Configurable salary rates per classification."""
    __tablename__ = "salary_classification_config"

    config_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    classification = Column(String(50), nullable=False, unique=True, index=True)
    daily_rate = Column(Float, nullable=False, default=0)
    annual_increase_pct = Column(Float, nullable=False, default=0)
    annual_increase_base = Column(Float, nullable=False, default=0)
    incentive_rate = Column(Float, nullable=False, default=0)
    increase_2025_rate = Column(Float, nullable=False, default=0)
    bonus_rate = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))