"""
SecureTrack Platform — Payroll Engine Routes
Full payroll: salary config, monthly generation from leader attendance, tax, insurance, pay slips.
"""
import uuid
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional
from calendar import monthrange

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_
from pydantic import BaseModel

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.attendance_log import AttendanceLog
from app.models.salary_config import SalaryConfig
from app.models.monthly_payroll import MonthlyPayroll
from app.models.cash_advance import CashAdvance
from app.models.leave_request import LeaveRequest
from app.models.deduction_rule import DeductionRule
from app.enums import UserRole

router = APIRouter()


# ══════════════════════════════════════════
# Egyptian Tax Brackets (2024/2025)
# ══════════════════════════════════════════
TAX_BRACKETS = [
    (15000, 0.0),     # First 15,000 EGP — exempt
    (30000, 0.025),   # 15,001 - 30,000 — 2.5%
    (45000, 0.10),    # 30,001 - 45,000 — 10%
    (60000, 0.15),    # 45,001 - 60,000 — 15%
    (200000, 0.20),   # 60,001 - 200,000 — 20%
    (400000, 0.225),  # 200,001 - 400,000 — 22.5%
    (float('inf'), 0.25),  # Above 400,000 — 25%
]


def calculate_annual_tax(annual_gross: float) -> float:
    """Calculate Egyptian income tax based on progressive brackets."""
    remaining = annual_gross
    total_tax = 0.0
    prev_limit = 0.0

    for limit, rate in TAX_BRACKETS:
        bracket_amount = min(remaining, limit - prev_limit)
        if bracket_amount <= 0:
            break
        total_tax += bracket_amount * rate
        remaining -= bracket_amount
        prev_limit = limit

    return round(total_tax, 2)


def calculate_monthly_tax(monthly_gross: float) -> float:
    """Monthly tax = annual tax / 12."""
    annual = monthly_gross * 12
    return round(calculate_annual_tax(annual) / 12, 2)


# ══════════════════════════════════════════
# Salary Config CRUD
# ══════════════════════════════════════════

class SalaryConfigCreate(BaseModel):
    classification: str
    label_en: Optional[str] = None
    daily_rate: float
    monthly_base: float = 0.0
    insurance_employee_share: float = 0.0
    incentive_rate: float = 0.0
    sort_order: int = 0


class SalaryConfigUpdate(BaseModel):
    daily_rate: Optional[float] = None
    monthly_base: Optional[float] = None
    insurance_employee_share: Optional[float] = None
    incentive_rate: Optional[float] = None
    label_en: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


@router.get("/salary-config", summary="List all salary configurations")
def list_salary_configs(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    configs = db.query(SalaryConfig).order_by(SalaryConfig.sort_order).all()
    return [
        {
            "config_id": c.config_id,
            "classification": c.classification,
            "label_en": c.label_en,
            "daily_rate": c.daily_rate,
            "monthly_base": c.monthly_base,
            "insurance_employee_share": c.insurance_employee_share,
            "incentive_rate": c.incentive_rate,
            "is_active": c.is_active,
            "sort_order": c.sort_order,
        }
        for c in configs
    ]


@router.post("/salary-config", status_code=201, summary="Create salary config")
def create_salary_config(
    payload: SalaryConfigCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    existing = db.query(SalaryConfig).filter(SalaryConfig.classification == payload.classification).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Classification '{payload.classification}' already exists")

    config = SalaryConfig(
        config_id=str(uuid.uuid4()),
        classification=payload.classification,
        label_en=payload.label_en,
        daily_rate=payload.daily_rate,
        monthly_base=payload.monthly_base,
        insurance_employee_share=payload.insurance_employee_share,
        incentive_rate=payload.incentive_rate,
        sort_order=payload.sort_order,
    )
    db.add(config)
    db.commit()
    return {"detail": "Salary config created", "config_id": config.config_id}


@router.put("/salary-config/{config_id}", summary="Update salary config")
def update_salary_config(
    config_id: str,
    payload: SalaryConfigUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    config = db.query(SalaryConfig).filter(SalaryConfig.config_id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(config, field, value)

    db.commit()
    return {"detail": "Config updated"}


# ══════════════════════════════════════════
# Tax Brackets Info
# ══════════════════════════════════════════

@router.get("/tax-brackets", summary="Get Egyptian tax bracket configuration")
def get_tax_brackets(
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    return {
        "country": "Egypt",
        "year": 2024,
        "brackets": [
            {"up_to": b[0] if b[0] != float('inf') else None, "rate": b[1], "rate_pct": f"{b[1]*100}%"}
            for b in TAX_BRACKETS
        ],
        "note": "Annual brackets. Monthly tax = annual_tax / 12",
    }


# ══════════════════════════════════════════
# Monthly Payroll Generation
# ══════════════════════════════════════════

@router.post("/generate/{year}/{month}", summary="Generate monthly payroll from leader attendance")
def generate_monthly_payroll(
    year: int,
    month: int,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """
    Generate monthly payroll for all guards/outdoor.
    Source of truth: Leader/Supervisor recorded attendance (AttendanceLog.status).
    
    Formula:
    - working_days = days_present + days_late (late counts as working but with deduction)
    - gross = working_days × daily_rate
    - deductions = absence + late + advances + insurance + tax
    - net = gross + incentive + bonus - deductions
    """
    # Check if already generated
    existing = db.query(MonthlyPayroll).filter(
        MonthlyPayroll.year == year, MonthlyPayroll.month == month
    ).first()
    if existing:
        # Delete old records and regenerate
        db.query(MonthlyPayroll).filter(
            MonthlyPayroll.year == year, MonthlyPayroll.month == month
        ).delete()
        db.flush()

    # Date range for this month
    _, days_in_month = monthrange(year, month)
    date_from = date(year, month, 1)
    date_to = date(year, month, days_in_month)

    # Get all guards/outdoor users
    employees = db.query(User).filter(
        User.role.in_(['guard', 'outdoor']),
        User.is_active == True,
    ).all()

    # Get salary configs for lookup
    salary_configs = {
        sc.classification: sc
        for sc in db.query(SalaryConfig).filter(SalaryConfig.is_active == True).all()
    }

    # Get deduction rules
    rules = {
        r.rule_type: r
        for r in db.query(DeductionRule).filter(DeductionRule.is_active == True).all()
    }
    absent_deduction_amount = rules.get('absent', None)
    late_deduction_rule = rules.get('late', None)

    # Get approved leaves for this month
    approved_leaves = db.query(LeaveRequest).filter(
        LeaveRequest.status.in_(['approved_by_supervisor', 'approved_by_ops_mgr', 'approved_by_hr']),
        LeaveRequest.start_date <= date_to,
        LeaveRequest.end_date >= date_from,
    ).all()

    # Build leave days per user
    leave_days_map: dict[str, int] = {}
    for leave in approved_leaves:
        user_id = leave.guard_id
        # Count overlapping days with this month
        start = max(leave.start_date, date_from) if isinstance(leave.start_date, date) else date_from
        end = min(leave.end_date, date_to) if isinstance(leave.end_date, date) else date_to
        days = (end - start).days + 1
        if days > 0:
            leave_days_map[user_id] = leave_days_map.get(user_id, 0) + days

    # Get cash advances for deduction
    advances = db.query(CashAdvance).filter(
        CashAdvance.status.in_(['admin_approved', 'supervisor_approved']),
    ).all()
    advance_map: dict[str, float] = {}
    for adv in advances:
        uid = adv.requester_id
        advance_map[uid] = advance_map.get(uid, 0) + float(adv.amount or 0)

    generated = []

    for emp in employees:
        # Get attendance logs from leader for this employee in this month
        rosters = (
            db.query(GuardRoster)
            .filter(
                GuardRoster.guard_id == emp.user_id,
                GuardRoster.assigned_date >= date_from,
                GuardRoster.assigned_date <= date_to,
            )
            .all()
        )

        roster_ids = [r.roster_id for r in rosters]
        logs = []
        if roster_ids:
            logs = (
                db.query(AttendanceLog)
                .filter(AttendanceLog.roster_id.in_(roster_ids))
                .all()
            )

        # Count by leader-recorded status
        days_present = sum(1 for l in logs if l.status == 'present')
        days_late = sum(1 for l in logs if l.status == 'late')
        days_absent = sum(1 for l in logs if l.status == 'absent')
        days_leave = leave_days_map.get(emp.user_id, 0)
        total_scheduled = len(rosters)

        # Look up salary config by classification
        emp_classification = emp.classification or 'فرد'
        config = salary_configs.get(emp_classification)
        daily_rate = config.daily_rate if config else (emp.daily_rate or 0.0)
        monthly_base = config.monthly_base if config else (emp.base_salary or 0.0)
        insurance_share = config.insurance_employee_share if config else 0.0
        incentive = config.incentive_rate if config else 0.0

        # Working days = present + late (late is still a working day)
        working_days = float(days_present + days_late)

        # Gross salary
        gross = round(working_days * daily_rate, 2)

        # Deductions
        absence_ded = 0.0
        if absent_deduction_amount:
            absence_ded = round(days_absent * absent_deduction_amount.amount, 2)
        else:
            absence_ded = round(days_absent * 200.0, 2)  # Default 200 EGP per absent day

        late_ded = 0.0
        if late_deduction_rule and late_deduction_rule.is_per_minute:
            # Estimate: each late day = 30 min average late
            late_ded = round(days_late * 30 * late_deduction_rule.amount, 2)
        else:
            late_ded = round(days_late * 50.0, 2)  # Default flat 50 EGP per late day

        advance_ded = advance_map.get(emp.user_id, 0.0)
        insurance_ded = insurance_share
        tax_ded = calculate_monthly_tax(gross)

        total_deductions = round(absence_ded + late_ded + advance_ded + insurance_ded + tax_ded, 2)
        total_additions = round(incentive, 2)
        net_salary = round(gross + total_additions - total_deductions, 2)

        payroll = MonthlyPayroll(
            payroll_id=str(uuid.uuid4()),
            user_id=emp.user_id,
            year=year,
            month=month,
            employee_name=emp.name,
            badge_number=emp.badge_number,
            classification=emp_classification,
            role=emp.role if isinstance(emp.role, str) else emp.role.value,
            days_present=days_present,
            days_absent=days_absent,
            days_late=days_late,
            days_leave=days_leave,
            total_scheduled_days=total_scheduled,
            working_days=working_days,
            daily_rate=daily_rate,
            base_salary=monthly_base,
            gross_salary=gross,
            absence_deduction=absence_ded,
            late_deduction=late_ded,
            advance_deduction=advance_ded,
            insurance_deduction=insurance_ded,
            tax_deduction=tax_ded,
            total_deductions=total_deductions,
            incentive=incentive,
            total_additions=total_additions,
            net_salary=net_salary,
        )
        db.add(payroll)
        generated.append(payroll)

    db.commit()

    return {
        "detail": f"Payroll generated for {year}/{month}",
        "employees_processed": len(generated),
        "total_net": round(sum(p.net_salary for p in generated), 2),
        "total_deductions": round(sum(p.total_deductions for p in generated), 2),
    }


# ══════════════════════════════════════════
# Monthly Payroll Retrieval
# ══════════════════════════════════════════

@router.get("/monthly/{year}/{month}", summary="Get monthly payroll data")
def get_monthly_payroll(
    year: int,
    month: int,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    records = db.query(MonthlyPayroll).filter(
        MonthlyPayroll.year == year, MonthlyPayroll.month == month
    ).order_by(MonthlyPayroll.employee_name).all()

    employees = []
    for r in records:
        employees.append({
            "payroll_id": r.payroll_id,
            "user_id": r.user_id,
            "employee_name": r.employee_name,
            "badge_number": r.badge_number,
            "classification": r.classification,
            "role": r.role,
            "days_present": r.days_present,
            "days_absent": r.days_absent,
            "days_late": r.days_late,
            "days_leave": r.days_leave,
            "total_scheduled_days": r.total_scheduled_days,
            "working_days": r.working_days,
            "daily_rate": r.daily_rate,
            "base_salary": r.base_salary,
            "gross_salary": r.gross_salary,
            "absence_deduction": r.absence_deduction,
            "late_deduction": r.late_deduction,
            "advance_deduction": r.advance_deduction,
            "insurance_deduction": r.insurance_deduction,
            "tax_deduction": r.tax_deduction,
            "total_deductions": r.total_deductions,
            "incentive": r.incentive,
            "bonus": r.bonus,
            "net_salary": r.net_salary,
            "is_finalized": r.is_finalized,
        })

    total_net = round(sum(e["net_salary"] for e in employees), 2)
    total_deductions = round(sum(e["total_deductions"] for e in employees), 2)
    total_gross = round(sum(e["gross_salary"] for e in employees), 2)

    return {
        "year": year,
        "month": month,
        "currency": "EGP",
        "employees": employees,
        "summary": {
            "total_employees": len(employees),
            "total_gross": total_gross,
            "total_deductions": total_deductions,
            "total_net": total_net,
            "is_finalized": all(e["is_finalized"] for e in employees) if employees else False,
        },
    }


# ══════════════════════════════════════════
# Finalize Payroll
# ══════════════════════════════════════════

@router.put("/finalize/{year}/{month}", summary="Lock/finalize monthly payroll")
def finalize_payroll(
    year: int,
    month: int,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    records = db.query(MonthlyPayroll).filter(
        MonthlyPayroll.year == year, MonthlyPayroll.month == month
    ).all()

    if not records:
        raise HTTPException(status_code=404, detail="No payroll found for this period")

    now = datetime.now(timezone.utc)
    for r in records:
        r.is_finalized = True
        r.finalized_at = now

    db.commit()
    return {"detail": f"Payroll for {year}/{month} finalized ({len(records)} records)"}


# ══════════════════════════════════════════
# Individual Pay Slip
# ══════════════════════════════════════════

@router.get("/slip/{user_id}/{year}/{month}", summary="Get individual pay slip")
def get_pay_slip(
    user_id: str,
    year: int,
    month: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get pay slip. Employees can view their own; admin/HR can view any."""
    if current_user.user_id != user_id:
        role = current_user.role if isinstance(current_user.role, str) else current_user.role.value
        if role not in ('admin', 'hr', 'accountant', 'ceo'):
            raise HTTPException(status_code=403, detail="Can only view your own pay slip")

    record = db.query(MonthlyPayroll).filter(
        MonthlyPayroll.user_id == user_id,
        MonthlyPayroll.year == year,
        MonthlyPayroll.month == month,
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Pay slip not found")

    return {
        "payroll_id": record.payroll_id,
        "employee_name": record.employee_name,
        "badge_number": record.badge_number,
        "classification": record.classification,
        "period": f"{year}/{month:02d}",
        "attendance": {
            "days_present": record.days_present,
            "days_absent": record.days_absent,
            "days_late": record.days_late,
            "days_leave": record.days_leave,
            "total_scheduled": record.total_scheduled_days,
            "working_days": record.working_days,
        },
        "earnings": {
            "daily_rate": record.daily_rate,
            "gross_salary": record.gross_salary,
            "incentive": record.incentive,
            "bonus": record.bonus,
            "overtime_pay": record.overtime_pay,
            "total_earnings": round(record.gross_salary + record.total_additions, 2),
        },
        "deductions": {
            "absence": record.absence_deduction,
            "late": record.late_deduction,
            "advance_repayment": record.advance_deduction,
            "insurance": record.insurance_deduction,
            "tax": record.tax_deduction,
            "other": record.other_deductions,
            "total_deductions": record.total_deductions,
        },
        "net_salary": record.net_salary,
        "currency": "EGP",
        "is_finalized": record.is_finalized,
    }


# ══════════════════════════════════════════
# Excel/CSV Export
# ══════════════════════════════════════════

@router.get("/export-csv/{year}/{month}", summary="Export monthly payroll as CSV")
def export_payroll_csv(
    year: int,
    month: int,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    records = db.query(MonthlyPayroll).filter(
        MonthlyPayroll.year == year, MonthlyPayroll.month == month
    ).order_by(MonthlyPayroll.employee_name).all()

    if not records:
        raise HTTPException(status_code=404, detail="No payroll data found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Badge", "Classification", "Role",
        "Days Present", "Days Late", "Days Absent", "Days Leave",
        "Working Days", "Daily Rate (EGP)",
        "Gross Salary", "Absence Ded.", "Late Ded.",
        "Advance Ded.", "Insurance", "Tax",
        "Total Deductions", "Incentive", "Net Salary (EGP)",
    ])

    for r in records:
        writer.writerow([
            r.employee_name,
            r.badge_number or "",
            r.classification or "",
            r.role or "",
            r.days_present,
            r.days_late,
            r.days_absent,
            r.days_leave,
            r.working_days,
            r.daily_rate,
            r.gross_salary,
            r.absence_deduction,
            r.late_deduction,
            r.advance_deduction,
            r.insurance_deduction,
            r.tax_deduction,
            r.total_deductions,
            r.incentive,
            r.net_salary,
        ])

    output.seek(0)
    filename = f"payroll_{year}_{month:02d}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
