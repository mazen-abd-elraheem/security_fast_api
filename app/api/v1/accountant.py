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
@router.post("/generate/{year:int}/{month:int}", summary="Generate payroll sheet for a month")
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

    # 5b. Bonuses approved THIS month
    bonuses_qs = db.query(Bonus).filter(
        Bonus.status == "approved",
        Bonus.created_at >= datetime.combine(month_start, datetime.min.time()),
        Bonus.created_at <= datetime.combine(month_end, datetime.max.time()),
    ).all()
    bonus_map: dict = {}
    for b in bonuses_qs:
        bonus_map[b.guard_id] = bonus_map.get(b.guard_id, 0) + float(b.amount or 0)

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

    # 8. Roster info (bulk: last assigned within this month per user)
    #    Bug fix: previously queried ALL rosters with no date filter, causing
    #    regenerated past months to show the employee's *current* site/shift.
    all_rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(
            GuardRoster.guard_id.in_(user_ids),
            GuardRoster.assigned_date >= month_start,
            GuardRoster.assigned_date <= month_end,
        )
        .order_by(GuardRoster.assigned_date.desc())
        .all()
    )
    roster_by_user: dict = {}
    for r in all_rosters:
        if r.guard_id not in roster_by_user:
            roster_by_user[r.guard_id] = r

    # ── Week boundaries — 4 chunks that cover the full month (no tail loss) ──
    week_ranges = []
    for w in range(4):
        ws = month_start + timedelta(days=w * 7)
        if w == 3:
            we = month_end
        else:
            we = ws + timedelta(days=6)
        if ws <= month_end:
            week_ranges.append((ws, we))

    # Pad to exactly 4 so compute_row always receives 4 weeks
    while len(week_ranges) < 4:
        week_ranges.append((month_end, month_end))

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
                "absent_excused": 0, "absent_unexcused": 0,
                "overtime": 0, "rest_allowance": 0,
                "late": 0, "deduction": 0,
                "rest": 0, "annual_leave": 0, "sick_leave": 0,
                "overtime_hours": 0.0,
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
        bns = bonus_map.get(u.user_id, 0)

        row_data = compute_row(
            user_data, attendance_data, adv, config_map,
            year, month, idx + 1,
            formula_configs=formula_configs,
            deduction_rules=deduction_rules,
            manual_bonus=bns,
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



@router.get("/{year:int}/{month:int}", summary="Get payroll spreadsheet data")
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


@router.put("/{year:int}/{month:int}", summary="Accountant edits cells in the payroll sheet")
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

    # Validate all fields before touching the DB (fast-fail)
    for upd in payload.updates:
        if upd.field not in editable_fields:
            raise HTTPException(400, f"Field '{upd.field}' is not editable")

    # Bulk-fetch all target rows in one query (avoids N+1)
    row_ids = [upd.row_id for upd in payload.updates]
    rows_qs = db.query(PayrollSheetRow).filter(
        PayrollSheetRow.row_id.in_(row_ids)
    ).all()
    row_map = {r.row_id: r for r in rows_qs}

    updated = 0
    for upd in payload.updates:
        row = row_map.get(upd.row_id)
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


@router.put("/{year:int}/{month:int}/approve", summary="Approve the monthly payroll")
async def approve_payroll(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Lock and approve the payroll sheet.
    Blocks if any transfer method has insufficient credit to cover net salaries.
    Deducts from TransferMethodCredit on successful approval.
    """
    from app.models.transfer_method_credit import TransferMethodCredit, TransferMethodCreditLog
    from app.models.notification import Notification

    rows = db.query(PayrollSheetRow).filter(
        and_(PayrollSheetRow.year == year, PayrollSheetRow.month == month)
    ).all()

    if not rows:
        raise HTTPException(404, "No payroll data found for this month")

    if rows[0].is_approved:
        raise HTTPException(400, "Already approved")

    # ── Group net_salary by transfer_method ──
    method_totals: Dict[str, float] = {}
    for r in rows:
        user = db.query(User).filter(User.user_id == r.user_id).first()
        method = (user.transfer_method or "").strip() if user else ""
        if not method:
            continue
        method_totals[method] = method_totals.get(method, 0.0) + float(r.net_salary or 0)

    # ── Check credits: block if any method is overdrawn ──
    reference = f"Payroll {year}-{month:02d}"
    shortfalls: List[Dict[str, Any]] = []
    credit_objects: Dict[str, Any] = {}

    for method_name, total_needed in method_totals.items():
        credit = db.query(TransferMethodCredit).filter(
            TransferMethodCredit.transfer_method_name == method_name
        ).first()
        if credit is None:
            # Try fuzzy match via method ID
            from app.models.accountant_models import TransferMethod as TM
            tm = db.query(TM).filter(TM.name == method_name).first()
            if tm:
                credit = db.query(TransferMethodCredit).filter(
                    TransferMethodCredit.transfer_method_id == tm.id
                ).first()

        if credit is None or credit.balance < total_needed:
            available = credit.balance if credit else 0.0
            shortfalls.append({
                "method": method_name,
                "needed": round(total_needed, 2),
                "available": round(available, 2),
                "shortfall": round(total_needed - available, 2),
            })
        else:
            credit_objects[method_name] = (credit, total_needed)

    if shortfalls:
        # Send admin notification for each shortfall
        admin_users = db.query(User).filter(User.role == "admin", User.is_active == True).all()
        for admin in admin_users:
            msg = f"Payroll {year}-{month:02d} BLOCKED: Insufficient credit. "
            for s in shortfalls:
                msg += f"{s['method']}: needs {s['needed']} EGP, available {s['available']} EGP (short {s['shortfall']} EGP). "
            notif = Notification(
                notification_id=str(uuid.uuid4()),
                user_id=admin.user_id,
                title=f"Payroll {year}-{month:02d} Blocked — Insufficient Credit",
                body=msg.strip(),
                type="payroll_credit_shortfall",
            )
            db.add(notif)
        db.commit()
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Payroll approval blocked: insufficient transfer method credit.",
                "shortfalls": shortfalls,
            }
        )

    # ── Approve and deduct credits ──
    now = datetime.now(timezone.utc)
    for r in rows:
        r.is_approved = True
        r.approved_by = current_user.user_id
        r.approved_at = now

    for method_name, (credit, total_deducted) in credit_objects.items():
        credit.balance = round(credit.balance - total_deducted, 2)
        credit.total_deducted = round(credit.total_deducted + total_deducted, 2)
        credit.updated_at = now
        log = TransferMethodCreditLog(
            id=str(uuid.uuid4()),
            credit_id=credit.id,
            operation="deduct",
            amount=total_deducted,
            balance_after=credit.balance,
            reference=reference,
            actor_id=current_user.user_id,
            actor_name=current_user.name,
            created_at=now,
        )
        db.add(log)

    db.commit()
    return {
        "message": f"Payroll for {year}/{month} approved",
        "approved_by": current_user.name,
        "rows": len(rows),
        "credits_deducted": [
            {"method": k, "amount_deducted": round(v[1], 2), "balance_after": round(v[0].balance, 2)}
            for k, v in credit_objects.items()
        ],
    }


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
    role_filter: Optional[str] = Query(None, description="Filter by role: guard, outdoor, supervisor, leader, lady, or all"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """
    Live payroll report using DailyAttendanceEntry (supervisor manual entries).
    Same compute_row engine as generate_payroll_sheet — zero hardcodes.
    Role scope unified with tax-sheet (SHEET_ROLES) so leader/lady aren't silently excluded.
    """
    # 1. Eligible users
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(SHEET_ROLES))
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

    # 5. Week boundaries — tail-inclusive so every day in the range is counted
    week_ranges = [
        (date_from + timedelta(days=w * 7),
         min(date_from + timedelta(days=(w + 1) * 7 - 1), date_to))
        for w in range(4)
        if date_from + timedelta(days=w * 7) <= date_to
    ]
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
    """
    Export payroll report as CSV.
    Now uses the same compute_row engine as /report and /generate so the numbers
    always match what the accountant approves in the grid.
    Role scope unified to SHEET_ROLES.
    """
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(SHEET_ROLES))
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

    if not users:
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=payroll_{date_from}_to_{date_to}.csv"},
        )

    user_ids = list(user_dict.keys())

    # Load formula engine configs (same as /report)
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
    late_rule = deduction_rules.get("late")
    late_threshold = int(late_rule.threshold_minutes) if late_rule else 10

    # Bulk load rosters (ordered so first entry in list is most recent)
    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(
            GuardRoster.guard_id.in_(user_ids),
            GuardRoster.assigned_date >= date_from,
            GuardRoster.assigned_date <= date_to,
        )
        .order_by(GuardRoster.assigned_date.desc())
        .all()
    )
    user_rosters: dict[str, list] = {u_id: [] for u_id in user_ids}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

    # Bulk load attendance entries
    all_entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(user_ids),
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        )
        .all()
    )
    user_entries: dict[str, list] = {u_id: [] for u_id in user_ids}
    for entry in all_entries:
        user_entries[entry.employee_id].append(entry)

    # Supervisor name lookup
    sup_ids = {e.entered_by for e in all_entries if e.entered_by}
    supervisors = db.query(User).filter(User.user_id.in_(sup_ids)).all()
    sup_dict = {s.user_id: s.name for s in supervisors}

    # Site lookup
    sites = db.query(Site).all()
    site_dict = {s.site_id: s.name for s in sites}
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    # Advances
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

    # Bulk load supervisor visits for all supervisor/leader users (eliminates N+1)
    sup_leader_ids = [
        u.user_id for u in users
        if (u.role.value if hasattr(u.role, 'value') else u.role) in ('supervisor', 'leader')
    ]
    all_visits = []
    if sup_leader_ids:
        all_visits = (
            db.query(SupervisorVisit)
            .options(joinedload(SupervisorVisit.site))
            .filter(
                SupervisorVisit.supervisor_id.in_(sup_leader_ids),
                SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                SupervisorVisit.is_verified == True,
            )
            .order_by(SupervisorVisit.check_in_time.asc())
            .all()
        )
    visits_by_user: dict = {}
    for v in all_visits:
        visits_by_user.setdefault(v.supervisor_id, {}).setdefault(v.check_in_time.date(), []).append(v)

    # Week boundaries — tail-inclusive
    week_ranges = [
        (date_from + timedelta(days=w * 7),
         min(date_from + timedelta(days=(w + 1) * 7 - 1), date_to))
        for w in range(4)
        if date_from + timedelta(days=w * 7) <= date_to
    ]
    while len(week_ranges) < 4:
        week_ranges.append((date_to, date_to))

    for idx, (user_id, user) in enumerate(user_dict.items(), start=1):
        e_list = user_entries[user_id]
        r_list = user_rosters[user_id]

        eff_dr = (
            user.daily_rate if (user.daily_rate and user.daily_rate > 0)
            else ((user.base_salary / 30) if (user.base_salary and user.base_salary > 0) else 0)
        )
        user_data = {
            "employee_code": user.employee_code or user.badge_number or "",
            "classification":  user.classification or user.role or "",
            "name":            user.name,
            "daily_rate":      eff_dr,
            "hire_date":       str(user.hire_date)[:10] if user.hire_date else "",
            "termination_date": "", "termination_reason": "",
            "insurance_status": user.insurance_status or "none",
            "bank_account":    user.bank_account or "",
            "transfer_name":   user.transfer_name or user.name or "",
            "transfer_method": user.transfer_method or "",
            "payroll_amount":  user.payroll_amount or 0,
            "shift_time": "", "supervisor_name": "", "site_name": "",
            "uniform_status": "", "employee_insurance": 0,
        }

        entry_by_date = {e.entry_date: e for e in e_list}
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
            date_from.year, date_from.month, idx,
            formula_configs=formula_configs,
            deduction_rules=deduction_rules,
        )

        # Travel allowance from pre-loaded visits (no per-user N+1)
        travel_allowance = 0.0
        role_str = user.role.value if hasattr(user.role, "value") else user.role
        user_visits_by_date = visits_by_user.get(user_id, {})
        for daily_visits in user_visits_by_date.values():
            if not daily_visits:
                continue
            first_site = daily_visits[0].site.name
            travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
            for i in range(1, len(daily_visits)):
                from_s = daily_visits[i - 1].site.name
                to_s = daily_visits[i].site.name
                travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
            last_site = daily_visits[-1].site.name
            travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)

        # Roster-derived metadata (ordered desc so first is most recent in period)
        primary_site = r_list[0].shift.site.name if r_list and r_list[0].shift and r_list[0].shift.site else "N/A"
        shift_label  = r_list[0].shift.label if r_list and r_list[0].shift else "N/A"
        primary_sup  = sup_dict.get(e_list[-1].entered_by, "N/A") if e_list and e_list[-1].entered_by else "N/A"
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
            row["total_absent_excused"],
            row["total_absent_unexcused"],
            round(row["total_overtime_hours"], 2),
            row.get("total_rest_allowance", 0),
            row["total_late"],
            round(row.get("manual_deduction", 0), 2),
            row.get("total_rest", 0),
            row.get("total_annual_leave", 0),
            row.get("total_sick_leave", 0),
            row["operational_days"],
            row["daily_rate"],
            round(travel_allowance, 2),
            row["gross_salary"],
            adv,
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
    """
    Export Bank Payroll report as CSV.
    Now uses compute_row engine (same numbers as accountant grid).
    Role scope unified to SHEET_ROLES.
    """
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(SHEET_ROLES))
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

    user_ids = list(user_dict.keys())

    # Formula engine (same as /report)
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
    late_rule = deduction_rules.get("late")
    late_threshold = int(late_rule.threshold_minutes) if late_rule else 10

    # Bulk load entries
    all_entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(user_ids),
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        )
        .all()
    )
    user_entries: dict[str, list] = {u_id: [] for u_id in user_ids}
    for entry in all_entries:
        user_entries[entry.employee_id].append(entry)

    # Advances
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

    # Travel fees
    sites = db.query(Site).all()
    base_site = next((s for s in sites if getattr(s, 'is_base', False)), None)
    base_site_name = base_site.name if base_site else "Base"
    travel_fees = db.query(TravelFee).filter(TravelFee.is_active == True).all()
    travel_fee_map = {(tf.from_site_name.lower(), tf.to_site_name.lower()): float(tf.amount) for tf in travel_fees}

    # Bulk load supervisor visits (no per-user N+1)
    sup_leader_ids = [
        u.user_id for u in users
        if (u.role.value if hasattr(u.role, 'value') else u.role) in ('supervisor', 'leader')
    ]
    visits_by_user: dict = {}
    if sup_leader_ids:
        all_visits = (
            db.query(SupervisorVisit)
            .options(joinedload(SupervisorVisit.site))
            .filter(
                SupervisorVisit.supervisor_id.in_(sup_leader_ids),
                SupervisorVisit.check_in_time >= datetime.combine(date_from, datetime.min.time()),
                SupervisorVisit.check_in_time <= datetime.combine(date_to, datetime.max.time()),
                SupervisorVisit.is_verified == True,
            )
            .order_by(SupervisorVisit.check_in_time.asc())
            .all()
        )
        for v in all_visits:
            visits_by_user.setdefault(v.supervisor_id, {}).setdefault(v.check_in_time.date(), []).append(v)

    # Week boundaries — tail-inclusive
    week_ranges = [
        (date_from + timedelta(days=w * 7),
         min(date_from + timedelta(days=(w + 1) * 7 - 1), date_to))
        for w in range(4)
        if date_from + timedelta(days=w * 7) <= date_to
    ]
    while len(week_ranges) < 4:
        week_ranges.append((date_to, date_to))

    total_sum = 0.0
    for idx, (user_id, user) in enumerate(user_dict.items(), start=1):
        e_list = user_entries[user_id]
        eff_dr = (
            user.daily_rate if (user.daily_rate and user.daily_rate > 0)
            else ((user.base_salary / 30) if (user.base_salary and user.base_salary > 0) else 0)
        )
        user_data = {
            "employee_code": user.employee_code or user.badge_number or "",
            "classification":  user.classification or user.role or "",
            "name":            user.name,
            "daily_rate":      eff_dr,
            "hire_date":       str(user.hire_date)[:10] if user.hire_date else "",
            "termination_date": "", "termination_reason": "",
            "insurance_status": user.insurance_status or "none",
            "bank_account":    user.bank_account or "",
            "transfer_name":   user.transfer_name or user.name or "",
            "transfer_method": user.transfer_method or "",
            "payroll_amount":  user.payroll_amount or 0,
            "shift_time": "", "supervisor_name": "", "site_name": "",
            "uniform_status": "", "employee_insurance": 0,
        }

        entry_by_date = {e.entry_date: e for e in e_list}
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
            date_from.year, date_from.month, idx,
            formula_configs=formula_configs,
            deduction_rules=deduction_rules,
        )

        # Travel allowance from pre-loaded visits
        travel_allowance = 0.0
        for daily_visits in visits_by_user.get(user_id, {}).values():
            if not daily_visits:
                continue
            first_site = daily_visits[0].site.name
            travel_allowance += travel_fee_map.get((base_site_name.lower(), first_site.lower()), 0.0)
            for i in range(1, len(daily_visits)):
                from_s = daily_visits[i - 1].site.name
                to_s = daily_visits[i].site.name
                travel_allowance += travel_fee_map.get((from_s.lower(), to_s.lower()), 0.0)
            last_site = daily_visits[-1].site.name
            travel_allowance += travel_fee_map.get((last_site.lower(), base_site_name.lower()), 0.0)

        net_pay = round(row["net_salary"] + travel_allowance, 2)
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


# ── Classifications CRUD ────────────────────────────────────────────────────

@router.get("/classifications", summary="List unique classification names from formula configs")
def list_classifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Return all unique classification names (excluding __tax__)."""
    rows = (
        db.query(PayrollFormulaConfig.classification)
        .filter(PayrollFormulaConfig.classification != "__tax__")
        .distinct()
        .all()
    )
    return {"classifications": sorted(set(r[0] for r in rows))}


@router.put("/classifications/rename", summary="Rename a classification across all formula configs")
def rename_classification(
    old_name: str = Query(..., description="Current classification name"),
    new_name: str = Query(..., description="New classification name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Rename a classification in PayrollFormulaConfig, SalaryClassificationConfig, and all users."""
    rows = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == old_name
    ).all()
    if not rows:
        raise HTTPException(404, f"Classification '{old_name}' not found")
    for r in rows:
        r.classification = new_name
    # Sync SalaryClassificationConfig (used as fallback in compute_row config_map)
    legacy_cfg = db.query(SalaryClassificationConfig).filter(
        SalaryClassificationConfig.classification == old_name
    ).first()
    if legacy_cfg:
        legacy_cfg.classification = new_name
    # Also update users who have this classification
    db.query(User).filter(User.classification == old_name).update(
        {"classification": new_name}, synchronize_session="fetch"
    )
    db.commit()
    return {"message": f"Renamed '{old_name}' → '{new_name}'", "updated_configs": len(rows)}


@router.post("/classifications/create", summary="Create a new classification with default formula keys")
def create_classification_with_keys(
    name: str = Query(..., description="New classification name"),
    daily_rate: float = Query(0.0, description="Daily rate for the legacy SalaryClassificationConfig fallback"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Create a new classification with all PayrollFormulaConfig keys set to 0
    AND a matching SalaryClassificationConfig entry so config_map lookups don't miss it.
    Function renamed from create_classification to avoid shadowing the POST /config/classifications handler.
    """
    existing = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == name
    ).first()
    if existing:
        raise HTTPException(409, f"Classification '{name}' already exists")
    for key in FORMULA_CONFIG_KEYS:
        db.add(PayrollFormulaConfig(
            classification=name,
            config_key=key,
            value=0.0,
            updated_by=current_user.user_id,
        ))
    # Also create/update legacy SalaryClassificationConfig so compute_row config_map is consistent
    legacy_cfg = db.query(SalaryClassificationConfig).filter(
        SalaryClassificationConfig.classification == name
    ).first()
    if not legacy_cfg:
        db.add(SalaryClassificationConfig(
            config_id=str(__import__('uuid').uuid4()),
            classification=name,
            daily_rate=daily_rate,
        ))
    db.commit()
    return {"message": f"Classification '{name}' created with {len(FORMULA_CONFIG_KEYS)} keys"}


@router.delete("/classifications/{name}", summary="Delete a classification and all its formula configs")
def delete_classification(
    name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    if name == "__tax__":
        raise HTTPException(400, "Cannot delete the tax bracket configuration")
    rows = db.query(PayrollFormulaConfig).filter(
        PayrollFormulaConfig.classification == name
    ).all()
    if not rows:
        raise HTTPException(404, f"Classification '{name}' not found")
    for r in rows:
        db.delete(r)
    db.commit()
    return {"message": f"Classification '{name}' deleted", "deleted_configs": len(rows)}


# ── Tax Sheet Endpoint ──────────────────────────────────────────────────────

SHEET_ROLES = ["guard", "outdoor", "supervisor", "lady", "leader"]

@router.get("/tax-sheet", summary="Tax sheet — full payroll summary with roster/shift/supervisor data")
def get_tax_sheet(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    role_filter: Optional[str] = Query(None, description="Filter by role"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """
    Full payroll sheet with shift label, supervisor, site, and all formula-driven columns.
    """
    # 1. Eligible users
    users_query = db.query(User).filter(User.is_active == True)
    if role_filter and role_filter != "all":
        users_query = users_query.filter(User.role == role_filter)
    else:
        users_query = users_query.filter(User.role.in_(SHEET_ROLES))
    users = users_query.all()
    if not users:
        return {
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "currency": CURRENCY, "employees": [],
            "summary": {"total_employees": 0, "grand_total_net_pay": 0.0},
        }
    user_ids = [u.user_id for u in users]
    user_dict = {u.user_id: u for u in users}

    # 2. DB configs
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
    late_rule = deduction_rules.get("late")
    late_threshold = int(late_rule.threshold_minutes) if late_rule else 10

    # 3. Roster → Shift → Site lookups
    roster_entries = (
        db.query(GuardRoster)
        .filter(
            GuardRoster.guard_id.in_(user_ids),
            GuardRoster.assigned_date >= date_from,
            GuardRoster.assigned_date <= date_to,
        ).all()
    )
    shift_ids = list(set(r.shift_id for r in roster_entries))
    shifts = db.query(Shift).filter(Shift.shift_id.in_(shift_ids)).all() if shift_ids else []
    shift_map = {s.shift_id: s for s in shifts}
    site_ids = list(set(s.site_id for s in shifts))
    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all() if site_ids else []
    site_map = {s.site_id: s.name for s in sites}

    # Map employee → shift label & site name (use latest roster entry)
    emp_shift_label: dict = {}
    emp_site_name: dict = {}
    for r in sorted(roster_entries, key=lambda x: x.assigned_date):
        shift_obj = shift_map.get(r.shift_id)
        if shift_obj:
            emp_shift_label[r.guard_id] = shift_obj.label or f"{shift_obj.start_time}-{shift_obj.end_time}"
            site_name = site_map.get(shift_obj.site_id, "")
            if site_name:
                emp_site_name[r.guard_id] = site_name

    # 4. Supervisor mapping via attendance entries
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

    supervisors = db.query(User).filter(User.role.in_(["supervisor", "leader"]), User.is_active == True).all()
    sup_name_map = {s.user_id: s.name for s in supervisors}
    emp_supervisor: dict = {}
    for entry in all_entries:
        if entry.entered_by and entry.entered_by in sup_name_map:
            emp_supervisor[entry.employee_id] = sup_name_map[entry.entered_by]

    # 5. Advances
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

    # 6. Week boundaries — tail-inclusive so every day in the range is counted
    week_ranges = [
        (date_from + timedelta(days=w * 7),
         min(date_from + timedelta(days=(w + 1) * 7 - 1), date_to))
        for w in range(4)
        if date_from + timedelta(days=w * 7) <= date_to
    ]
    while len(week_ranges) < 4:
        week_ranges.append((date_to, date_to))

    # 7. Compute per employee
    employees_out = []
    for idx, (user_id, user) in enumerate(user_dict.items()):
        eff_dr = (
            user.daily_rate if (user.daily_rate and user.daily_rate > 0)
            else ((user.base_salary / 30) if (user.base_salary and user.base_salary > 0) else 0)
        )
        user_data = {
            "employee_code": user.employee_code or user.badge_number or "",
            "classification": user.classification or user.role or "",
            "name": user.name,
            "daily_rate": eff_dr,
            "hire_date": str(user.hire_date)[:10] if user.hire_date else "",
            "termination_date": "", "termination_reason": "",
            "insurance_status": user.insurance_status or "none",
            "bank_account": user.bank_account or "",
            "transfer_name": user.transfer_name or user.name or "",
            "transfer_method": user.transfer_method or "",
            "payroll_amount": user.payroll_amount or 0,
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
            "employee_code":     user.employee_code or user.badge_number or "",
            "name":              user.name,
            "role":              user.role.value if hasattr(user.role, 'value') else user.role,
            "classification":    user_data["classification"],
            "shift_label":       emp_shift_label.get(user_id, ""),
            "supervisor_name":   emp_supervisor.get(user_id, ""),
            "site_name":         emp_site_name.get(user_id, ""),
            "daily_rate":        row["daily_rate"],
            "base_salary":       user.base_salary or 0,
            "incentive":         row["incentive"],
            "current_year_raise": row["increase_2025"],
            "bonus":             row["bonus_rate"] if "bonus_rate" in row else row.get("total_incentive", 0),
            "allowances":        row["allowances"],
            "total_income":      row["total_income"],
            "employee_insurance": row["employee_insurance"],
            "insurance_share":   row["insurance_share"],
            "tax_deduction":     row["tax_deduction"],
            "advances":          adv,
            "total_deductions":  round(row["tax_deduction"] + row.get("advance_deduction", adv) + row["insurance_share"], 2),
            "net_salary":        row["net_salary"],
            "operational_days":  row["operational_days"],
            "days_absent":       row["total_absent_excused"] + row["total_absent_unexcused"],
            "overtime_hours":    row["total_overtime_hours"],
            "gross_salary":      row["gross_salary"],
            "annual_increase_base": row.get("annual_increase_base", 0),
            "annual_increase_pct": row.get("annual_increase_pct", 0),
            "monthly_salary":    row.get("monthly_salary", 0),
            "actual_salary":     row.get("actual_salary", 0),
            "annual_personal_exemption": row.get("annual_personal_exemption", 0),
            "net_after_insurance": row.get("net_after_insurance", 0),
            "annual_taxable":    row.get("annual_taxable", 0),
            "annual_tax":        row.get("annual_tax", 0),
        })

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "currency": CURRENCY,
        "employees": employees_out,
        "summary": {
            "total_employees": len(employees_out),
            "grand_total_net_pay": round(sum(e["net_salary"] for e in employees_out), 2),
            "grand_total_deductions": round(sum(e["total_deductions"] for e in employees_out), 2),
        },
    }


class TaxSheetCellUpdate(BaseModel):
    user_id: str
    field: str
    value: float


class TaxSheetBatchUpdate(BaseModel):
    updates: List[TaxSheetCellUpdate]


@router.patch("/tax-sheet/update", summary="Batch update tax sheet cells")
def update_tax_sheet_cells(
    payload: TaxSheetBatchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
):
    """Update editable cells in the tax sheet (daily_rate, base_salary, etc.)."""
    updated = 0
    for item in payload.updates:
        user = db.query(User).filter(User.user_id == item.user_id).first()
        if not user:
            continue
        if item.field == "daily_rate":
            user.daily_rate = item.value
        elif item.field == "base_salary":
            user.base_salary = item.value
        elif item.field == "payroll_amount":
            user.payroll_amount = item.value
        else:
            continue
        updated += 1
    db.commit()
    return {"message": f"Updated {updated} cells"}


# ══════════════════════════════════════════════════════════
#  Transfer Method Credit Management
# ══════════════════════════════════════════════════════════

from app.models.transfer_method_credit import (
    TransferMethodCredit, TransferMethodCreditLog, TransferMethodTopUpRequest
)
from app.models.accountant_models import TransferMethod as TransferMethodModel


class TopUpDirectPayload(BaseModel):
    amount: float
    reference: Optional[str] = None


class TopUpRequestPayload(BaseModel):
    amount: float
    reference: Optional[str] = None
    notes: Optional[str] = None


class ReviewTopUpPayload(BaseModel):
    approve: bool
    review_notes: Optional[str] = None


@router.get("/transfer-credits", summary="List all transfer methods with current credit balances")
def list_transfer_credits(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    """Returns every transfer method alongside its credit balance and stats."""
    methods = db.query(TransferMethodModel).order_by(TransferMethodModel.sort_order).all()
    result = []
    for m in methods:
        credit = db.query(TransferMethodCredit).filter(
            TransferMethodCredit.transfer_method_id == m.id
        ).first()
        pending_requests = 0
        if credit:
            pending_requests = db.query(TransferMethodTopUpRequest).filter(
                TransferMethodTopUpRequest.credit_id == credit.id,
                TransferMethodTopUpRequest.status == "pending",
            ).count()
        result.append({
            "method_id": m.id,
            "name": m.name,
            "name_ar": m.name_ar,
            "is_active": m.is_active,
            "credit_id": credit.id if credit else None,
            "balance": round(credit.balance, 2) if credit else 0.0,
            "total_topped_up": round(credit.total_topped_up, 2) if credit else 0.0,
            "total_deducted": round(credit.total_deducted, 2) if credit else 0.0,
            "pending_top_up_requests": pending_requests,
            "updated_at": credit.updated_at.isoformat() if credit and credit.updated_at else None,
        })
    return {"credits": result}


@router.post("/transfer-credits/{method_id}/top-up", summary="Admin: directly top up a transfer method credit")
def admin_top_up_credit(
    method_id: str,
    payload: TopUpDirectPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin directly adds credit to a transfer method."""
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    method = db.query(TransferMethodModel).filter(TransferMethodModel.id == method_id).first()
    if not method:
        raise HTTPException(404, "Transfer method not found")

    credit = db.query(TransferMethodCredit).filter(
        TransferMethodCredit.transfer_method_id == method_id
    ).first()
    if not credit:
        credit = TransferMethodCredit(
            id=str(uuid.uuid4()),
            transfer_method_id=method_id,
            transfer_method_name=method.name,
            balance=0.0,
            total_topped_up=0.0,
            total_deducted=0.0,
        )
        db.add(credit)
        db.flush()

    now = datetime.now(timezone.utc)
    credit.balance = round(credit.balance + payload.amount, 2)
    credit.total_topped_up = round(credit.total_topped_up + payload.amount, 2)
    credit.updated_at = now

    log = TransferMethodCreditLog(
        id=str(uuid.uuid4()),
        credit_id=credit.id,
        operation="admin_top_up",
        amount=payload.amount,
        balance_after=credit.balance,
        reference=payload.reference or "Admin direct top-up",
        actor_id=current_user.user_id,
        actor_name=current_user.name,
        created_at=now,
    )
    db.add(log)
    db.commit()
    return {
        "message": f"Topped up {method.name} by {payload.amount} EGP",
        "new_balance": credit.balance,
        "method": method.name,
    }


@router.post("/transfer-credits/{method_id}/request-top-up", status_code=201,
             summary="Accountant: submit a top-up request for admin approval")
def request_top_up(
    method_id: str,
    payload: TopUpRequestPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ACCOUNTANT, UserRole.ADMIN)),
):
    """Accountant submits a credit top-up request; Admin must approve it."""
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    method = db.query(TransferMethodModel).filter(TransferMethodModel.id == method_id).first()
    if not method:
        raise HTTPException(404, "Transfer method not found")

    credit = db.query(TransferMethodCredit).filter(
        TransferMethodCredit.transfer_method_id == method_id
    ).first()
    if not credit:
        credit = TransferMethodCredit(
            id=str(uuid.uuid4()),
            transfer_method_id=method_id,
            transfer_method_name=method.name,
            balance=0.0, total_topped_up=0.0, total_deducted=0.0,
        )
        db.add(credit)
        db.flush()

    req = TransferMethodTopUpRequest(
        id=str(uuid.uuid4()),
        credit_id=credit.id,
        requested_amount=payload.amount,
        reference=payload.reference,
        notes=payload.notes,
        status="pending",
        requested_by=current_user.user_id,
        requested_by_name=current_user.name,
    )
    db.add(req)

    # Notify all admins
    from app.models.notification import Notification
    admins = db.query(User).filter(User.role == "admin", User.is_active == True).all()
    for admin in admins:
        notif = Notification(
            notification_id=str(uuid.uuid4()),
            user_id=admin.user_id,
            title=f"Top-Up Request: {method.name}",
            body=f"{current_user.name} requested a credit top-up of {payload.amount:,.0f} EGP for {method.name}. Reference: {payload.reference or 'N/A'}",
            type="top_up_request",
        )
        db.add(notif)

    db.commit()
    return {"message": "Top-up request submitted", "request_id": req.id, "status": "pending"}


@router.get("/transfer-credits/top-up-requests", summary="Admin: list all pending top-up requests")
def list_top_up_requests(
    status: Optional[str] = Query(None, description="Filter: pending | approved | rejected"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO)),
):
    q = db.query(TransferMethodTopUpRequest)
    if status:
        q = q.filter(TransferMethodTopUpRequest.status == status)
    else:
        q = q.filter(TransferMethodTopUpRequest.status == "pending")
    requests = q.order_by(TransferMethodTopUpRequest.requested_at.desc()).all()
    result = []
    for r in requests:
        result.append({
            "id": r.id,
            "credit_id": r.credit_id,
            "transfer_method": r.credit.transfer_method_name if r.credit else "",
            "requested_amount": r.requested_amount,
            "reference": r.reference,
            "notes": r.notes,
            "status": r.status,
            "requested_by": r.requested_by_name,
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            "reviewed_by": r.reviewed_by_name,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_notes": r.review_notes,
        })
    return {"requests": result}


@router.post("/transfer-credits/top-up-requests/{request_id}/review",
             summary="Admin: approve or reject a top-up request")
def review_top_up_request(
    request_id: str,
    payload: ReviewTopUpPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    req = db.query(TransferMethodTopUpRequest).filter(
        TransferMethodTopUpRequest.id == request_id
    ).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "pending":
        raise HTTPException(400, f"Request is already {req.status}")

    now = datetime.now(timezone.utc)
    req.reviewed_by = current_user.user_id
    req.reviewed_by_name = current_user.name
    req.reviewed_at = now
    req.review_notes = payload.review_notes

    if payload.approve:
        req.status = "approved"
        credit = db.query(TransferMethodCredit).filter(
            TransferMethodCredit.id == req.credit_id
        ).first()
        if credit:
            credit.balance = round(credit.balance + req.requested_amount, 2)
            credit.total_topped_up = round(credit.total_topped_up + req.requested_amount, 2)
            credit.updated_at = now
            log = TransferMethodCreditLog(
                id=str(uuid.uuid4()),
                credit_id=credit.id,
                operation="top_up",
                amount=req.requested_amount,
                balance_after=credit.balance,
                reference=req.reference or f"Approved request #{req.id[:8]}",
                actor_id=current_user.user_id,
                actor_name=current_user.name,
                created_at=now,
            )
            db.add(log)
        db.commit()
        return {
            "message": "Request approved and credit applied",
            "new_balance": credit.balance if credit else None,
        }
    else:
        req.status = "rejected"
        db.commit()
        return {"message": "Request rejected"}


@router.get("/transfer-credits/{credit_id}/logs", summary="Get audit log for a transfer method credit")
def get_credit_logs(
    credit_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
):
    total = db.query(TransferMethodCreditLog).filter(
        TransferMethodCreditLog.credit_id == credit_id
    ).count()
    logs = (
        db.query(TransferMethodCreditLog)
        .filter(TransferMethodCreditLog.credit_id == credit_id)
        .order_by(TransferMethodCreditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "logs": [
            {
                "id": l.id,
                "operation": l.operation,
                "amount": l.amount,
                "balance_after": l.balance_after,
                "reference": l.reference,
                "actor": l.actor_name,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }
