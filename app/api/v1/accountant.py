"""
SecureTrack - Accountant Excel View API
Generates, stores, edits, and approves the monthly payroll spreadsheet.
"""
import uuid
import json
from datetime import datetime, timezone, date, timedelta
from calendar import monthrange

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.models.user import User
from app.models.payroll_sheet_row import PayrollSheetRow, SalaryClassificationConfig
from app.models.cash_advance import CashAdvance
from app.models.attendance_log import AttendanceLog
from app.models.guard_roster import GuardRoster
from app.enums import UserRole
from app.services.payroll_formulas import compute_row

router = APIRouter()


# -- Generate & Get Excel View --
@router.post("/generate/{year}/{month}", summary="Generate payroll sheet for a month")
async def generate_payroll_sheet(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Generate or regenerate the full payroll spreadsheet for a month.
    Computes all formulas and saves permanently to DB."""

    # Delete existing un-approved rows for this month (allow regeneration)
    existing = db.query(PayrollSheetRow).filter(
        and_(PayrollSheetRow.year == year, PayrollSheetRow.month == month)
    ).all()

    approved_rows = [r for r in existing if r.is_approved]
    if approved_rows:
        raise HTTPException(400, "This month's payroll has already been approved. Cannot regenerate.")

    for r in existing:
        db.delete(r)
    db.flush()

    # Load config overrides
    configs = db.query(SalaryClassificationConfig).all()
    config_map = {}
    for c in configs:
        config_map[c.classification] = {
            "daily_rate": c.daily_rate,
            "annual_increase_pct": c.annual_increase_pct,
            "annual_increase_base": c.annual_increase_base,
            "incentive_rate": c.incentive_rate,
            "increase_2025_rate": c.increase_2025_rate,
            "bonus_rate": c.bonus_rate,
        }

    # Get all active users (guards, supervisors, etc.)
    users = db.query(User).filter(User.is_active == True).all()

    # Get cash advances for this month
    advances = db.query(CashAdvance).filter(
        CashAdvance.status.in_(["admin_approved", "admin_modified", "supervisor_approved"])
    ).all()
    advance_map = {}
    for a in advances:
        uid = a.guard_id if hasattr(a, 'guard_id') else (a.user_id if hasattr(a, 'user_id') else None)
        if uid:
            advance_map[uid] = advance_map.get(uid, 0) + float(a.approved_amount or a.amount or 0)

    # -- Build weekly attendance from attendance_logs --
    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end = date(year, month, days_in_month)

    # Week boundaries (7-day chunks)
    week_ranges = []
    for w in range(4):
        ws = month_start + timedelta(days=w * 7)
        we = min(month_start + timedelta(days=(w + 1) * 7 - 1), month_end)
        week_ranges.append((ws, we))

    rows_created = []
    for idx, u in enumerate(users):
        # Build user dict for compute_row
        # Use role as classification; daily_rate from base_salary/30 or user.daily_rate
        effective_daily_rate = u.daily_rate if (u.daily_rate and u.daily_rate > 0) else ((u.base_salary / 30) if (u.base_salary and u.base_salary > 0) else 0)
        user_data = {
            "employee_code": u.employee_code or u.badge_number or "",
            "classification": u.classification or u.role or "",
            "name": u.name,
            "daily_rate": effective_daily_rate,
            "hire_date": str(u.hire_date)[:10] if u.hire_date else "",
            "termination_date": "",
            "termination_reason": "",
            "insurance_status": u.insurance_status or "none",
            "bank_account": u.bank_account or "",
            "transfer_name": u.transfer_name or u.name or "",
            "transfer_method": u.transfer_method or "",
            "payroll_amount": u.payroll_amount or 0,
            "shift_time": "",
            "supervisor_name": "",
            "site_name": "",
            "uniform_status": "",
            "employee_insurance": 0,
        }

        # Get roster info for site/supervisor/shift
        from sqlalchemy.orm import joinedload
        roster = db.query(GuardRoster).options(
            joinedload(GuardRoster.shift)
        ).filter(
            GuardRoster.guard_id == u.user_id,
        ).order_by(GuardRoster.assigned_date.desc()).first()
        if roster:
            if roster.shift and hasattr(roster.shift, 'site') and roster.shift.site:
                user_data["site_name"] = roster.shift.site.name
            if roster.shift:
                user_data["shift_time"] = roster.shift.label if hasattr(roster.shift, 'label') else ""

        # -- Query attendance_logs for this user this month --
        user_logs = (
            db.query(AttendanceLog)
            .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
            .filter(
                GuardRoster.guard_id == u.user_id,
                GuardRoster.assigned_date >= month_start,
                GuardRoster.assigned_date <= month_end,
            )
            .all()
        )

        # Build a date->status map
        date_status = {}
        for log in user_logs:
            if log.roster and log.roster.assigned_date:
                d = log.roster.assigned_date
                date_status[d] = log.status  # present, absent, late, replacement

        # Aggregate into 4 weeks
        attendance_data = []
        for ws, we in week_ranges:
            week = {
                "absent_excused": 0, "absent_unexcused": 0,
                "overtime": 0, "rest_allowance": 0,
                "late": 0, "deduction": 0,
                "rest": 0, "annual_leave": 0, "sick_leave": 0,
            }
            d = ws
            while d <= we:
                status = date_status.get(d, None)
                if status == "absent":
                    week["absent_unexcused"] += 1
                elif status == "late":
                    week["late"] += 1
                # present/replacement = working day (no deduction)
                d += timedelta(days=1)
            attendance_data.append(week)

        adv = advance_map.get(u.user_id, 0)

        # Compute all columns with real attendance data
        row_data = compute_row(user_data, attendance_data, adv, config_map, year, month, idx + 1)
        row_data["user_id"] = u.user_id
        row_data["year"] = year
        row_data["month"] = month
        row_data["row_id"] = str(uuid.uuid4())

        row = PayrollSheetRow(**row_data)
        db.add(row)
        rows_created.append(row_data)

    db.commit()
    return {"message": f"Generated {len(rows_created)} payroll rows for {year}/{month}", "count": len(rows_created)}


@router.get("/{year}/{month}", summary="Get payroll spreadsheet data")
async def get_payroll_sheet(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Returns all rows of the payroll spreadsheet for a month. Fast — reads from DB."""
    rows = db.query(PayrollSheetRow).filter(
        and_(PayrollSheetRow.year == year, PayrollSheetRow.month == month)
    ).order_by(PayrollSheetRow.serial_no).all()

    if not rows:
        return {"rows": [], "is_approved": False, "count": 0}

    result = []
    for r in rows:
        d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        d["overridden_fields"] = json.loads(r.overridden_fields) if r.overridden_fields else []
        result.append(d)

    return {
        "rows": result,
        "is_approved": rows[0].is_approved if rows else False,
        "approved_by": rows[0].approved_by if rows else None,
        "approved_at": str(rows[0].approved_at) if rows and rows[0].approved_at else None,
        "count": len(result),
    }


class CellUpdate(BaseModel):
    row_id: str
    field: str
    value: float


class BatchUpdate(BaseModel):
    updates: List[CellUpdate]


@router.put("/{year}/{month}", summary="Accountant edits cells in the payroll sheet")
async def update_payroll_cells(
    year: int,
    month: int,
    payload: BatchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.ADMIN, UserRole.CEO)),
):
    """Accountant edits one or more cells. Tracks which fields were manually overridden."""
    editable_fields = {
        "w1_absent_excused", "w1_absent_unexcused", "w1_overtime", "w1_rest_allowance",
        "w1_late", "w1_deduction", "w1_rest", "w1_annual_leave", "w1_sick_leave",
        "w2_absent_excused", "w2_absent_unexcused", "w2_overtime", "w2_rest_allowance",
        "w2_late", "w2_deduction", "w2_rest", "w2_annual_leave", "w2_sick_leave",
        "w3_absent_excused", "w3_absent_unexcused", "w3_overtime", "w3_rest_allowance",
        "w3_late", "w3_deduction", "w3_rest", "w3_annual_leave", "w3_sick_leave",
        "w4_absent_excused", "w4_absent_unexcused", "w4_overtime", "w4_rest_allowance",
        "w4_late", "w4_deduction", "w4_rest", "w4_annual_leave", "w4_sick_leave",
        "manual_deduction", "bonus", "bonus_deduction", "cash_payment",
        "advance_deduction", "other_deductions", "payroll_amount",
        "total_work_days", "operational_days", "daily_rate",
        "salary_from_ops", "annual_increase_current", "annual_increase_prev",
        "gross_salary", "insurance_share", "tax_deduction", "net_salary",
        "incentive", "increase_2025", "total_incentive",
        "salary_diff", "total_salary_diff_incentive", "bonus_rounded", "grand_incentive",
    }

    updated = 0
    for upd in payload.updates:
        if upd.field not in editable_fields:
            raise HTTPException(400, f"Field '{upd.field}' is not editable")

        row = db.query(PayrollSheetRow).filter(
            PayrollSheetRow.row_id == upd.row_id
        ).first()
        if not row:
            continue
        if row.is_approved:
            raise HTTPException(400, "Cannot edit approved payroll")

        setattr(row, upd.field, upd.value)

        # Track overrides
        overrides = json.loads(row.overridden_fields) if row.overridden_fields else []
        if upd.field not in overrides:
            overrides.append(upd.field)
        row.overridden_fields = json.dumps(overrides)
        updated += 1

    db.commit()
    return {"message": f"Updated {updated} cells", "updated": updated}


@router.put("/{year}/{month}/approve", summary="Approve the monthly payroll")
async def approve_payroll(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Lock and approve the payroll sheet. No more edits after this."""
    rows = db.query(PayrollSheetRow).filter(
        and_(PayrollSheetRow.year == year, PayrollSheetRow.month == month)
    ).all()

    if not rows:
        raise HTTPException(404, "No payroll data found for this month")

    if rows[0].is_approved:
        raise HTTPException(400, "Already approved")

    now = datetime.now(timezone.utc)
    for r in rows:
        r.is_approved = True
        r.approved_by = current_user.user_id
        r.approved_at = now

    db.commit()
    return {"message": f"Payroll for {year}/{month} approved", "approved_by": current_user.name, "rows": len(rows)}


# -- Salary Classification Config CRUD --
class ClassificationConfigCreate(BaseModel):
    classification: str
    daily_rate: float
    annual_increase_pct: float = 0.3
    annual_increase_base: float = 500
    incentive_rate: float = 1100
    increase_2025_rate: float = 600
    bonus_rate: float = 1750


@router.get("/config/classifications", summary="Get all salary classification configs")
async def get_classifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    configs = db.query(SalaryClassificationConfig).all()
    return [
        {c.name: getattr(cfg, c.name) for c in cfg.__table__.columns}
        for cfg in configs
    ]


@router.post("/config/classifications", status_code=201, summary="Create classification config")
async def create_classification(
    payload: ClassificationConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
):
    existing = db.query(SalaryClassificationConfig).filter(
        SalaryClassificationConfig.classification == payload.classification
    ).first()
    if existing:
        raise HTTPException(400, f"Classification '{payload.classification}' already exists")

    cfg = SalaryClassificationConfig(
        config_id=str(uuid.uuid4()),
        classification=payload.classification,
        daily_rate=payload.daily_rate,
        annual_increase_pct=payload.annual_increase_pct,
        annual_increase_base=payload.annual_increase_base,
        incentive_rate=payload.incentive_rate,
        increase_2025_rate=payload.increase_2025_rate,
        bonus_rate=payload.bonus_rate,
    )
    db.add(cfg)
    db.commit()
    return {"config_id": cfg.config_id, "classification": cfg.classification}


@router.put("/config/classifications/{config_id}", summary="Update classification config")
async def update_classification(
    config_id: str,
    payload: ClassificationConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
):
    cfg = db.query(SalaryClassificationConfig).filter(
        SalaryClassificationConfig.config_id == config_id
    ).first()
    if not cfg:
        raise HTTPException(404, "Config not found")

    cfg.classification = payload.classification
    cfg.daily_rate = payload.daily_rate
    cfg.annual_increase_pct = payload.annual_increase_pct
    cfg.annual_increase_base = payload.annual_increase_base
    cfg.incentive_rate = payload.incentive_rate
    cfg.increase_2025_rate = payload.increase_2025_rate
    cfg.bonus_rate = payload.bonus_rate
    db.commit()
    return {"message": "Updated", "config_id": config_id}