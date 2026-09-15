"""
SecureTrack - Accountant Excel View API
Generates, stores, edits, and approves the monthly payroll spreadsheet.
"""
import uuid
import json
import csv
import io
from datetime import datetime, timezone, date, timedelta
from calendar import monthrange

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
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
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.site import Site
from app.models.shift import Shift
from app.models.supervisor_visit import SupervisorVisit
from app.models.travel_fee import TravelFee
from app.models.payroll_formula_config import PayrollFormulaConfig, DEFAULT_FORMULA_SEED, FORMULA_CONFIG_KEYS
from app.enums import UserRole
from app.services.payroll_formulas import (
    compute_row, load_formula_configs, load_deduction_rules,
    build_tax_brackets, calc_overtime_pay,
)

CURRENCY = "EGP"

# Payroll deduction constants
LATE_THRESHOLD_MINUTES = 10          # Grace period before counted as "late"
LATE_DEDUCTION_PER_MINUTE = 1.0      # EGP deducted per minute of lateness
ABSENT_DEDUCTION = 100.0             # EGP deducted per unexcused absent day

router = APIRouter()


# -- Generate & Get Excel View --
@router.post("/generate/{year}/{month}", summary="Generate payroll sheet for a month")
async def generate_payroll_sheet(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """
    Generate (or regenerate) the full payroll spreadsheet for a given month.
    Attendance source: DailyAttendanceEntry (supervisor manual entries).
    Cash advances: approved within this calendar month.
    All formulas: loaded from PayrollFormulaConfig + DeductionRule (zero hardcodes).
    """

    # ── Guard: prevent regeneration of approved months ──
    existing = db.query(PayrollSheetRow).filter(
        and_(PayrollSheetRow.year == year, PayrollSheetRow.month == month)
    ).all()
    approved_rows = [r for r in existing if r.is_approved]
    if approved_rows:
        raise HTTPException(400, "This month's payroll has already been approved. Cannot regenerate.")
    for r in existing:
        db.delete(r)
    db.flush()

    # ── Load all config in bulk (parallelised at DB level) ──
    _, days_in_month = monthrange(year, month)
    month_start = date(year, month, 1)
    month_end   = date(year, month, days_in_month)

    # 1. DB formula configs (replaces hardcoded maps)
    formula_configs = load_formula_configs(db)

    # 2. Deduction rules
    deduction_rules = load_deduction_rules(db)

    # 3. Legacy SalaryClassificationConfig (fallback)
    configs = db.query(SalaryClassificationConfig).all()
    config_map = {
        c.classification: {
            "daily_rate":           c.daily_rate,
            "annual_increase_pct":  c.annual_increase_pct,
            "annual_increase_base": c.annual_increase_base,
            "incentive_rate":       c.incentive_rate,
            "increase_2025_rate":   c.increase_2025_rate,
            "bonus_rate":           c.bonus_rate,
        }
        for c in configs
    }

    # 4. Active employees
    users = db.query(User).filter(User.is_active == True).all()
    user_ids = [u.user_id for u in users]

    # 5. Cash advances approved THIS month
    from sqlalchemy import func
    advances_qs = db.query(CashAdvance).filter(
        CashAdvance.status.in_(["admin_approved", "admin_modified", "supervisor_approved"]),
        CashAdvance.updated_at >= datetime.combine(month_start, datetime.min.time()),
        CashAdvance.updated_at <= datetime.combine(month_end, datetime.max.time()),
    ).all()
    advance_map: dict = {}
    for a in advances_qs:
        uid = getattr(a, 'guard_id', None) or getattr(a, 'user_id', None)
        if uid:
            advance_map[uid] = advance_map.get(uid, 0) + float(
                a.approved_amount or a.amount or 0
            )

    # 6. ALL DailyAttendanceEntry for this month (bulk load)
    all_entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(user_ids),
            DailyAttendanceEntry.entry_date >= month_start,
            DailyAttendanceEntry.entry_date <= month_end,
        )
        .all()
    )
    # Group by employee_id
    entries_by_user: dict = {uid: [] for uid in user_ids}
    for e in all_entries:
        entries_by_user[e.employee_id].append(e)

    # 7. Late threshold from DeductionRule (or 10 min default)
    late_rule = deduction_rules.get("late")
    late_threshold = int(late_rule.threshold_minutes) if late_rule else 10

    # 8. Roster info (bulk: last assigned per user)
    all_rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_ids))
        .order_by(GuardRoster.assigned_date.desc())
        .all()
    )
    roster_by_user: dict = {}
    for r in all_rosters:
        if r.guard_id not in roster_by_user:
            roster_by_user[r.guard_id] = r

    # ── Week boundaries (4 × 7-day chunks) ──
    week_ranges = []
    for w in range(4):
        ws = month_start + timedelta(days=w * 7)
        we = min(month_start + timedelta(days=(w + 1) * 7 - 1), month_end)
        week_ranges.append((ws, we))

    # ── Compute one row per employee ──
    rows_created = []
    for idx, u in enumerate(users):
        effective_daily_rate = (
            u.daily_rate if (u.daily_rate and u.daily_rate > 0)
            else ((u.base_salary / 30) if (u.base_salary and u.base_salary > 0) else 0)
        )

        # Resolve site/shift from roster
        site_name  = ""
        shift_time = ""
        roster = roster_by_user.get(u.user_id)
        if roster:
            if roster.shift:
                shift_time = getattr(roster.shift, 'label', '') or ''
                if hasattr(roster.shift, 'site') and roster.shift.site:
                    site_name = roster.shift.site.name

        user_data = {
            "employee_code":    u.employee_code or u.badge_number or "",
            "classification":   u.classification or u.role or "",
            "name":             u.name,
            "daily_rate":       effective_daily_rate,
            "hire_date":        str(u.hire_date)[:10] if u.hire_date else "",
            "termination_date": "",
            "termination_reason": "",
            "insurance_status": u.insurance_status or "none",
            "bank_account":     u.bank_account or "",
            "transfer_name":    u.transfer_name or u.name or "",
            "transfer_method":  u.transfer_method or "",
            "payroll_amount":   u.payroll_amount or 0,
            "shift_time":       shift_time,
            "supervisor_name": "",
            "site_name":        site_name,
            "uniform_status":  "",
            "employee_insurance": 0,
        }

        # ── Map DailyAttendanceEntry → 4-week attendance dicts ──
        user_entries = entries_by_user.get(u.user_id, [])
        entry_by_date: dict = {e.entry_date: e for e in user_entries}

        attendance_data = []
        for ws, we in week_ranges:
            week = {
                "absent_excused":   0, "absent_unexcused": 0,
                "overtime":         0, "overtime_hours":   0.0,
                "rest_allowance":   0,
                "late":             0, "deduction":        0,
                "rest":             0, "annual_leave":     0,
                "sick_leave":       0,
            }
            d = ws
            while d <= we:
                entry = entry_by_date.get(d)
                if entry:
                    s = entry.status
                    if s == "absence_excused":
                        week["absent_excused"] += 1
                    elif s == "absence_unexcused":
                        week["absent_unexcused"] += 1
                    elif s == "annual_leave":
                        week["annual_leave"] += 1
                    elif s == "sick_leave":
                        week["sick_leave"] += 1
                    elif s == "rest":
                        week["rest"] += 1
                    elif s == "rest_day_worked":
                        week["rest_allowance"] += 1
                    # present = default, no change
                    if entry.late_minutes and entry.late_minutes > late_threshold:
                        week["late"] += 1
                    if entry.overtime_hours and entry.overtime_hours > 0:
                        week["overtime"] += 1
                        week["overtime_hours"] += entry.overtime_hours
                d += timedelta(days=1)
            attendance_data.append(week)

        adv = advance_map.get(u.user_id, 0)

        row_data = compute_row(
            user_data, attendance_data, adv, config_map,
            year, month, idx + 1,
            formula_configs=formula_configs,
            deduction_rules=deduction_rules,
        )
        row_data["user_id"] = u.user_id
        row_data["year"]    = year
        row_data["month"]   = month
        row_data["row_id"]  = str(uuid.uuid4())

        # Remove any keys not in PayrollSheetRow columns
        valid_cols = {c.name for c in PayrollSheetRow.__table__.columns}
        row_data = {k: v for k, v in row_data.items() if k in valid_cols}

        db.add(PayrollSheetRow(**row_data))
        rows_created.append(row_data)

    db.commit()
    return {
        "message": f"Generated {len(rows_created)} payroll rows for {year}/{month}",
        "count": len(rows_created),
    }



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


# ══════════════════════════════════════════════════════════
#  PayrollFormulaConfig CRUD  (replaces hardcoded maps)
# ══════════════════════════════════════════════════════════

class FormulaConfigUpsert(BaseModel):
    classification: str
    config_key: str
    value: float


@router.get("/formula-configs", summary="List all payroll formula configs")
async def list_formula_configs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Return all PayrollFormulaConfig rows grouped by classification."""
    rows = db.query(PayrollFormulaConfig).filter(PayrollFormulaConfig.is_active == True).all()
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r.classification, {})
        grouped[r.classification][r.config_key] = {
            "id": r.id,
            "value": r.value,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
    return {"formula_configs": grouped}


@router.put("/formula-configs", summary="Upsert (create or update) a formula config value")
async def upsert_formula_config(
    payload: FormulaConfigUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
):
    """Set a single formula config value for a classification. Creates if not exists."""
    if payload.config_key not in FORMULA_CONFIG_KEYS and not payload.config_key.startswith("bracket_"):
        raise HTTPException(400, f"Unknown config_key '{payload.config_key}'. Valid keys: {FORMULA_CONFIG_KEYS}")

    row = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == payload.classification,
        PayrollFormulaConfig.config_key == payload.config_key,
    ).first()
    if row:
        row.value = payload.value
        row.updated_by = current_user.user_id
    else:
        row = PayrollFormulaConfig(
            classification=payload.classification,
            config_key=payload.config_key,
            value=payload.value,
            updated_by=current_user.user_id,
        )
        db.add(row)
    db.commit()
    return {"message": "Saved", "id": row.id, "classification": row.classification, "config_key": row.config_key, "value": row.value}


@router.delete("/formula-configs/{config_id}", summary="Delete a formula config entry")
async def delete_formula_config(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    row = db.query(PayrollFormulaConfig).filter(PayrollFormulaConfig.id == config_id).first()
    if not row:
        raise HTTPException(404, "Formula config not found")
    db.delete(row)
    db.commit()
    return {"message": "Deleted", "id": config_id}


@router.post("/formula-configs/seed", summary="Seed default formula configs from built-in defaults")
async def seed_formula_configs(
    overwrite: bool = Query(False, description="If true, overwrite existing values"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Seed PayrollFormulaConfig with the built-in default values."""
    created = 0
    updated = 0
    for cls, key, val in DEFAULT_FORMULA_SEED:
        row = db.query(PayrollFormulaConfig).filter(
            PayrollFormulaConfig.classification == cls,
            PayrollFormulaConfig.config_key == key,
        ).first()
        if row:
            if overwrite:
                row.value = val
                row.updated_by = current_user.user_id
                updated += 1
        else:
            db.add(PayrollFormulaConfig(
                classification=cls,
                config_key=key,
                value=val,
                updated_by=current_user.user_id,
            ))
            created += 1
    db.commit()
    return {"message": f"Seed complete", "created": created, "updated": updated}


@router.get("/report", summary="Payroll report for date range")
async def get_payroll_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None, description="Filter by role: guard, outdoor, supervisor, or all"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """
    Live payroll report using DailyAttendanceEntry (supervisor manual entries).
    Same compute_row engine as generate_payroll_sheet — zero hardcodes.
    """
    # 1. Eligible users
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    users = users_query.all()
    if not users:
        return {
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "currency": CURRENCY, "employees": [],
            "summary": {"total_employees": 0, "grand_total_deductions": 0.0, "grand_total_net_pay": 0.0},
        }
    user_ids  = [u.user_id for u in users]
    user_dict = {u.user_id: u for u in users}

    # 2. DB configs (zero hardcodes)
    formula_configs = load_formula_configs(db)
    deduction_rules = load_deduction_rules(db)
    configs = db.query(SalaryClassificationConfig).all()
    config_map = {
        c.classification: {
            "daily_rate": c.daily_rate, "annual_increase_pct": c.annual_increase_pct,
            "annual_increase_base": c.annual_increase_base, "incentive_rate": c.incentive_rate,
            "increase_2025_rate": c.increase_2025_rate, "bonus_rate": c.bonus_rate,
        }
        for c in configs
    }
    late_rule      = deduction_rules.get("late")
    late_threshold = int(late_rule.threshold_minutes) if late_rule else 10

    # 3. Bulk load entries
    all_entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(user_ids),
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        ).all()
    )
    entries_by_user: dict = {uid: [] for uid in user_ids}
    for e in all_entries:
        entries_by_user[e.employee_id].append(e)

    # 4. Advances in date range
    advances_qs = db.query(CashAdvance).filter(
        CashAdvance.status.in_(["admin_approved", "admin_modified", "supervisor_approved"]),
        CashAdvance.updated_at >= datetime.combine(date_from, datetime.min.time()),
        CashAdvance.updated_at <= datetime.combine(date_to, datetime.max.time()),
    ).all()
    advance_map: dict = {}
    for a in advances_qs:
        uid = getattr(a, 'guard_id', None) or getattr(a, 'user_id', None)
        if uid:
            advance_map[uid] = advance_map.get(uid, 0) + float(a.approved_amount or a.amount or 0)

    # 5. Week boundaries
    week_ranges = []
    for w in range(4):
        ws = date_from + timedelta(days=w * 7)
        we = min(date_from + timedelta(days=(w + 1) * 7 - 1), date_to)
        if ws > date_to:
            break
        week_ranges.append((ws, we))
    while len(week_ranges) < 4:
        week_ranges.append((date_to, date_to))

    # 6. Compute per employee
    employees_out = []
    for idx, (user_id, user) in enumerate(user_dict.items()):
        eff_dr = (
            user.daily_rate if (user.daily_rate and user.daily_rate > 0)
            else ((user.base_salary / 30) if (user.base_salary and user.base_salary > 0) else 0)
        )
        user_data = {
            "employee_code":    user.employee_code or user.badge_number or "",
            "classification":   user.classification or user.role or "",
            "name":             user.name,
            "daily_rate":       eff_dr,
            "hire_date":        str(user.hire_date)[:10] if user.hire_date else "",
            "termination_date": "", "termination_reason": "",
            "insurance_status": user.insurance_status or "none",
            "bank_account":     user.bank_account or "",
            "transfer_name":    user.transfer_name or user.name or "",
            "transfer_method":  user.transfer_method or "",
            "payroll_amount":   user.payroll_amount or 0,
            "shift_time": "", "supervisor_name": "", "site_name": "",
            "uniform_status": "", "employee_insurance": 0,
        }

        entry_by_date = {e.entry_date: e for e in entries_by_user.get(user_id, [])}
        attendance_data = []
        for ws, we in week_ranges:
            week = {
                "absent_excused": 0, "absent_unexcused": 0,
                "overtime": 0, "overtime_hours": 0.0,
                "rest_allowance": 0, "late": 0, "deduction": 0,
                "rest": 0, "annual_leave": 0, "sick_leave": 0,
            }
            d = ws
            while d <= we:
                entry = entry_by_date.get(d)
                if entry:
                    s = entry.status
                    if s == "absence_excused":     week["absent_excused"] += 1
                    elif s == "absence_unexcused": week["absent_unexcused"] += 1
                    elif s == "annual_leave":      week["annual_leave"] += 1
                    elif s == "sick_leave":        week["sick_leave"] += 1
                    elif s == "rest":              week["rest"] += 1
                    elif s == "rest_day_worked":   week["rest_allowance"] += 1
                    if entry.late_minutes and entry.late_minutes > late_threshold:
                        week["late"] += 1
                    if entry.overtime_hours and entry.overtime_hours > 0:
                        week["overtime"] += 1
                        week["overtime_hours"] += entry.overtime_hours
                d += timedelta(days=1)
            attendance_data.append(week)

        adv = advance_map.get(user_id, 0)
        row = compute_row(
            user_data, attendance_data, adv, config_map,
            date_from.year, date_from.month, idx + 1,
            formula_configs=formula_configs,
            deduction_rules=deduction_rules,
        )

        employees_out.append({
            "user_id":           user_id,
            "name":              user.name,
            "role":              user.role.value if hasattr(user.role, 'value') else user.role,
            "badge_number":      user.badge_number,
            "bank_account":      user.bank_account or "",
            "classification":    user_data["classification"],
            "transfer_method":   user.transfer_method or "",
            "base_salary":       user.base_salary or 0,
            "daily_rate":        row["daily_rate"],
            "operational_days":  row["operational_days"],
            "days_present":      row["total_work_days"] - row["total_absent_excused"] - row["total_absent_unexcused"],
            "days_absent":       row["total_absent_excused"] + row["total_absent_unexcused"],
            "days_late":         row["total_late"],
            "overtime_hours":    row["total_overtime_hours"],
            "advances":          adv,
            "gross_salary":      row["gross_salary"],
            "incentive":         row["incentive"],
            "increase_2025":     row["increase_2025"],
            "total_incentive":   row["total_incentive"],
            "allowances":        row["allowances"],
            "total_income":      row["total_income"],
            "employee_insurance": row["employee_insurance"],
            "insurance_share":   row["insurance_share"],
            "tax_deduction":     row["tax_deduction"],
            "advance_deduction": row["advance_deduction"],
            "total_deductions":  round(row["tax_deduction"] + row["advance_deduction"] + row["insurance_share"], 2),
            "net_salary":        row["net_salary"],
            "net_pay":           row["net_salary"],   # alias for Flutter compat
            "monthly_tax":       row["monthly_tax"],
            "annual_tax":        row["annual_tax"],
            "travel_allowance":  0.0,
            "currency":          CURRENCY,
        })

    return {
        "date_from":  date_from.isoformat(),
        "date_to":    date_to.isoformat(),
        "currency":   CURRENCY,
        "employees":  employees_out,
        "summary": {
            "total_employees":        len(employees_out),
            "grand_total_deductions": round(sum(e["total_deductions"] for e in employees_out), 2),
            "grand_total_net_pay":    round(sum(e["net_salary"] for e in employees_out), 2),
        },
    }




@router.get("/export", summary="Export payroll as CSV")
def export_payroll_csv(

    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export payroll report as CSV with 21 Arabic columns."""
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    headers = [
        "الاكواد", "مسلسل", "التصنيف", "توقيت\\nالعمل", "المشرف", "مشروع",
        "تاريخ \\nالتعيين", "الاســـــــــــــــــــــــــــــم", "غياب \\nباذن", "غياب \\nبدون", "اضافى", "بدل \\ن\nراحه", "تاخير", "خصم", "راحة",
        "اجازة \\nمن \\nالسنوي", "اجازة\\نمرضي", "ايام \\nالعمل \\nالتشغيليه",
        "الاجر\\ناليومية", "بدل \\nانتقالات", "اجمالي الراتب", "السلف"
    ]
    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(headers)

    # If no users, return empty CSV
    if not users:
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=payroll_{date_from}_to_{date_to}.csv"},
        )

    # Gather rosters and entries similar to report
    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_dict.keys()))
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )
    user_rosters: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )
    user_entries: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    # Site / Supervisor lookup
    sup_ids = {e.entered_by for e in entries}
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all()
    sup_dict = {s.user_id: s.name for s in supervisors}
    sites = db.query(Site).all()
    site_dict = {s.site_id: s.name for s in sites}
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    for idx, (user_id, user) in enumerate(user_dict.items(), start=1):
        r_list = user_rosters[user_id]
        e_list = user_entries[user_id]

        base_salary = getattr(user, "base_salary", None) or 0.0
        daily_rate = round(base_salary / 30, 2) if base_salary else 0.0

        days_excused = sum(1 for e in e_list if e.status == 'absence_excused')
        days_unexcused = sum(1 for e in e_list if e.status == 'absence_unexcused')
        days_present = sum(1 for e in e_list if e.status == 'present')
        days_rest = sum(1 for e in e_list if e.status == 'rest')
        days_rest_worked = sum(1 for e in e_list if e.status == 'rest_day_worked')
        days_annual = sum(1 for e in e_list if e.status == 'annual_leave')
        days_sick = sum(1 for e in e_list if e.status == 'sick_leave')
        days_late = sum(1 for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)

        overtime_hours = sum(e.overtime_hours for e in e_list)
        advances = sum(e.advance_amount for e in e_list)

        # Site / Shift / Supervisor logic
        primary_site = "N/A"
        shift_label = "N/A"
        if r_list:
            if r_list[0].shift and r_list[0].shift.site:
                primary_site = r_list[0].shift.site.name
            shift_label = r_list[0].shift.label if r_list[0].shift else "N/A"
        elif e_list:
            primary_site = site_dict.get(e_list[-1].site_id, "N/A")
        primary_sup = "N/A"
        if e_list:
            primary_sup = sup_dict.get(e_list[-1].entered_by, "N/A")

        # Deductions
        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_unexcused * ABSENT_DEDUCTION
        total_deductions = round(late_deduction + absent_deduction, 2)

        travel_allowance = 0.0
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        if role_str in ['supervisor', 'leader']:
            visits = (
                db.query(SupervisorVisit)
                .options(joinedload(SupervisorVisit.site))
                .filter(
                    SupervisorVisit.supervisor_id == user.user_id,
                    SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                    SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                    SupervisorVisit.is_verified == True,
                )
                .order_by(SupervisorVisit.check_in_time.asc())
                .all()
            )
            visits_by_date = {}
            for v in visits:
                d = v.check_in_time.date()
                visits_by_date.setdefault(d, []).append(v)
            for d, daily_visits in visits_by_date.items():
                if not daily_visits:
                    continue
                first_site = daily_visits[0].site.name
                travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
                for i in range(1, len(daily_visits)):
                    from_s = daily_visits[i-1].site.name
                    to_s = daily_visits[i].site.name
                    travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
                last_site = daily_visits[-1].site.name
                travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)

        net_pay = round(base_salary + travel_allowance - total_deductions - advances, 2)

        hire_date_str = user.hire_date.strftime('%Y-%m-%d') if user.hire_date else (user.created_at.strftime('%Y-%m-%d') if user.created_at else "N/A")

        writer.writerow([
            user.badge_number or "N/A",
            idx,
            role_str,
            shift_label,
            primary_sup,
            primary_site,
            hire_date_str,
            user.name,
            days_excused,
            days_unexcused,
            round(overtime_hours, 2),
            days_rest_worked,
            days_late,
            total_deductions,
            days_rest,
            days_annual,
            days_sick,
            len(r_list) if len(r_list) > 0 else len(e_list),
            daily_rate,
            round(travel_allowance, 2),
            net_pay,
            advances,
        ])

    output.seek(0)
    filename = f"payroll_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.get("/export-bank", summary="Export Bank Payroll as CSV")
def export_bank_payroll_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export Bank Payroll report as CSV."""
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(["guard", "outdoor", "supervisor"]))
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    headers = ["الكود", "الاسم", "رقم الحساب", "المبلغ"]
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(headers)

    if not users:
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=bank_payroll_{date_from}_to_{date_to}.csv"},
        )

    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )
    user_entries: dict[str, list] = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    sites = db.query(Site).all()
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    total_sum = 0.0
    for user_id, user in user_dict.items():
        e_list = user_entries[user_id]
        base_salary = getattr(user, "base_salary", None) or 0.0
        days_unexcused = sum(1 for e in e_list if e.status == 'absence_unexcused')
        advances = sum(e.advance_amount for e in e_list)
        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in e_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_unexcused * ABSENT_DEDUCTION
        total_deductions = round(late_deduction + absent_deduction, 2)
        travel_allowance = 0.0
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        if role_str in ['supervisor', 'leader']:
            visits = (
                db.query(SupervisorVisit)
                .options(joinedload(SupervisorVisit.site))
                .filter(
                    SupervisorVisit.supervisor_id == user.user_id,
                    SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                    SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                    SupervisorVisit.is_verified == True,
                )
                .order_by(SupervisorVisit.check_in_time.asc())
                .all()
            )
            visits_by_date = {}
            for v in visits:
                d = v.check_in_time.date()
                visits_by_date.setdefault(d, []).append(v)
            for d, daily_visits in visits_by_date.items():
                if not daily_visits:
                    continue
                first_site = daily_visits[0].site.name
                travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
                for i in range(1, len(daily_visits)):
                    from_s = daily_visits[i-1].site.name
                    to_s = daily_visits[i].site.name
                    travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
                last_site = daily_visits[-1].site.name
                travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)
        net_pay = round(base_salary + travel_allowance - total_deductions - advances, 2)
        total_sum += net_pay
        writer.writerow([
            user.badge_number or "N/A",
            user.name,
            getattr(user, "bank_account", "") or "",
            net_pay,
        ])

    writer.writerow(["", "", "اجمالي المرتبات", round(total_sum, 2)])
    output.seek(0)
    filename = f"bank_payroll_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.put("/salary/{user_id}", summary="Update base salary")
def update_salary(
    user_id: str,
    base_salary: float = Query(..., ge=0, description="New base salary in EGP"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Update a user's base salary."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    user.base_salary = base_salary
    db.commit()
    return {
        "detail": f"Salary updated to {base_salary} EGP",
        "user_id": user_id,
        "base_salary": base_salary,
    }

@router.put("/bank-account/{user_id}", summary="Update bank account number")
def update_bank_account(
    user_id: str,
    bank_account: str = Query(..., description="New bank account number"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Update a user's bank account number."""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    user.bank_account = bank_account
    db.commit()
    return {
        "detail": "Bank account updated successfully",
        "user_id": user_id,
    }
