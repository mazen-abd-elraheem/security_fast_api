"""
SecureTrack — Cash Advance Sheet API
Dedicated endpoint that joins users, attendance, evaluations, deductions,
cash advances, and rest allowance configs into a single report.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.site import Site
from app.models.shift import Shift
from app.models.guard_roster import GuardRoster
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.guard_evaluation import GuardEvaluation
from app.models.cash_advance import CashAdvance
from app.models.rest_allowance_config import RestAllowanceConfig
from app.enums import UserRole

router = APIRouter()

# Roles that can appear in the cash advance sheet
SHEET_ROLES = ["guard", "outdoor", "leader", "supervisor", "lady"]

# Arabic role labels
ROLE_LABELS_AR = {
    "guard": "حارس",
    "outdoor": "خارجي",
    "leader": "قائد",
    "supervisor": "مشرف",
    "lady": "سيدة",
    "admin": "مدير",
    "ceo": "رئيس تنفيذي",
    "accountant": "محاسب",
    "operations_manager": "مدير عمليات",
    "hr": "موارد بشرية",
    "personnel_officer": "شؤون أفراد",
}


# ── Schemas ──

class BatchUpdateItem(BaseModel):
    user_id: str
    field: str  # "deduction_override", "rest_allowance_override", "advance_amount_override"
    value: float


class BatchUpdateRequest(BaseModel):
    updates: list[BatchUpdateItem]


class ReviewRequest(BaseModel):
    notes: Optional[str] = None


# ── Helper: build employee sheet data ──

def _build_sheet_data(
    db: Session,
    date_from: date,
    date_to: date,
    tab: str,  # "approved", "rejected", "pending"
) -> list[dict]:
    """Build the full sheet data joining all sources."""

    # 1) Get cash advances filtered by tab
    if tab == "approved":
        status_filter = ["admin_approved", "ceo_approved", "admin_modified"]
    elif tab == "rejected":
        status_filter = ["ops_rejected", "admin_rejected", "ceo_rejected"]
    else:  # pending / in_progress
        status_filter = ["pending", "ops_approved"]

    advances = (
        db.query(CashAdvance)
        .filter(CashAdvance.status.in_(status_filter))
        .all()
    )

    # Group advances by guard_id
    advances_by_guard: dict[str, list] = defaultdict(list)
    for a in advances:
        advances_by_guard[a.guard_id].append(a)

    # Get unique employee IDs from advances
    employee_ids = list(advances_by_guard.keys())

    if not employee_ids:
        return []

    # 2) Fetch employees
    employees = (
        db.query(User)
        .filter(User.user_id.in_(employee_ids))
        .all()
    )
    emp_map = {e.user_id: e for e in employees}

    # 3) Get attendance summary per employee in date range
    attendance_entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(employee_ids),
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        )
        .all()
    )

    # Aggregate attendance per employee
    att_summary: dict[str, dict] = defaultdict(lambda: {
        "excused_absence": 0,
        "unexcused_absence": 0,
        "overtime_hours": 0.0,
        "rest_day_worked": 0,
        "late_minutes": 0,
        "rest_days": 0,
        "annual_leave": 0,
        "sick_leave": 0,
        "total_advance_amount": 0.0,
    })

    for entry in attendance_entries:
        s = att_summary[entry.employee_id]
        if entry.status == "absence_excused":
            s["excused_absence"] += 1
        elif entry.status == "absence_unexcused":
            s["unexcused_absence"] += 1
        elif entry.status == "rest_day_worked":
            s["rest_day_worked"] += 1
        elif entry.status == "rest":
            s["rest_days"] += 1
        elif entry.status == "annual_leave":
            s["annual_leave"] += 1
        elif entry.status == "sick_leave":
            s["sick_leave"] += 1

        s["overtime_hours"] += entry.overtime_hours or 0.0
        s["late_minutes"] += entry.late_minutes or 0
        s["total_advance_amount"] += entry.advance_amount or 0.0

    # 4) Get evaluations in date range
    evaluations = (
        db.query(
            GuardEvaluation.guard_id,
            func.avg(GuardEvaluation.overall_score).label("avg_score"),
        )
        .filter(GuardEvaluation.guard_id.in_(employee_ids))
        .group_by(GuardEvaluation.guard_id)
        .all()
    )
    eval_map = {e.guard_id: round(float(e.avg_score), 2) for e in evaluations}

    # 5) Get rest allowance config per role
    rest_configs = db.query(RestAllowanceConfig).filter(
        RestAllowanceConfig.is_active == True
    ).all()
    rest_rate_map = {rc.role: rc.rate_per_day for rc in rest_configs}

    # 6) Get supervisor assignments — find the most recent roster and its site's supervisor
    # We look for roster entries in the date range to find the guard's site
    roster_entries = (
        db.query(GuardRoster)
        .filter(
            GuardRoster.guard_id.in_(employee_ids),
            GuardRoster.assigned_date >= date_from,
            GuardRoster.assigned_date <= date_to,
        )
        .all()
    )

    # Get shift → site mapping
    shift_ids = list(set(r.shift_id for r in roster_entries))
    shifts = db.query(Shift).filter(Shift.shift_id.in_(shift_ids)).all() if shift_ids else []
    shift_site_map = {s.shift_id: s.site_id for s in shifts}

    # Get site details
    site_ids = list(set(shift_site_map.values()))
    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all() if site_ids else []
    site_map = {s.site_id: s.name for s in sites}

    # Map employee → site name (use latest roster entry)
    emp_site_map: dict[str, str] = {}
    for r in sorted(roster_entries, key=lambda x: x.assigned_date):
        site_id = shift_site_map.get(r.shift_id)
        if site_id:
            emp_site_map[r.guard_id] = site_map.get(site_id, "")

    # Find supervisors per site — supervisors who have roster entries at the same site
    # For simplicity, we look for users with role supervisor/leader who have attendance entries
    # at the same site
    supervisors = (
        db.query(User)
        .filter(User.role.in_(["supervisor", "leader"]), User.is_active == True)
        .all()
    )
    sup_name_map = {s.user_id: s.name for s in supervisors}

    # Map employee → supervisor name via DailyAttendanceEntry.entered_by
    emp_supervisor_map: dict[str, str] = {}
    for entry in attendance_entries:
        if entry.entered_by and entry.entered_by in sup_name_map:
            emp_supervisor_map[entry.employee_id] = sup_name_map[entry.entered_by]

    # 7) Build result rows
    result = []
    for eid in employee_ids:
        emp = emp_map.get(eid)
        if not emp:
            continue

        att = att_summary[eid]
        guard_advances = advances_by_guard[eid]

        # Calculate total advance amount (ops_approved or admin_approved)
        total_advance = sum(
            (a.approved_amount or a.amount) for a in guard_advances
            if a.status in ("ops_approved", "admin_approved", "admin_modified", "ceo_approved")
        )

        # Calculate rest allowance
        role = emp.role or "guard"
        rest_rate = rest_rate_map.get(role, 0.0)
        rest_allowance = att["rest_day_worked"] * rest_rate

        # Classification: use user's classification field or Arabic role label
        classification = emp.classification or ROLE_LABELS_AR.get(role, role)

        # Hire date
        hire_date = None
        if emp.hire_date:
            hire_date = emp.hire_date.isoformat() if hasattr(emp.hire_date, 'isoformat') else str(emp.hire_date)
        elif emp.created_at:
            hire_date = emp.created_at.strftime("%Y-%m-%d")

        # Rejection info (for rejected tab)
        rejection_notes = []
        if tab == "rejected":
            for a in guard_advances:
                notes = a.ops_manager_notes or a.admin_notes or a.ceo_notes or ""
                if notes:
                    rejection_notes.append(notes)

        row = {
            "user_id": eid,
            "employee_code": emp.employee_code or "",
            "badge_number": emp.badge_number or "",
            "classification": classification,
            "name": emp.name or "",
            "supervisor_name": emp_supervisor_map.get(eid, ""),
            "site_name": emp_site_map.get(eid, ""),
            "hire_date": hire_date or "",
            "excused_absence": att["excused_absence"],
            "unexcused_absence": att["unexcused_absence"],
            "overtime_hours": round(att["overtime_hours"], 1),
            "rest_allowance": round(rest_allowance, 2),
            "rest_allowance_days": att["rest_day_worked"],
            "late_minutes": att["late_minutes"],
            "deductions": 0.0,  # Will be calculated from payroll if available
            "rest_days": att["rest_days"],
            "annual_leave": att["annual_leave"],
            "sick_leave": att["sick_leave"],
            "evaluation_score": eval_map.get(eid, 0.0),
            "advance_amount": round(total_advance, 2),
            "rejection_notes": "; ".join(rejection_notes) if rejection_notes else "",
            # Include advance details for actions
            "advances": [
                {
                    "advance_id": a.advance_id,
                    "amount": a.amount,
                    "approved_amount": a.approved_amount,
                    "status": a.status,
                    "leader_name": a.leader_name,
                    "site_name": a.site_name,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "ops_manager_notes": getattr(a, "ops_manager_notes", None),
                    "admin_notes": a.admin_notes,
                    "ceo_notes": getattr(a, "ceo_notes", None),
                }
                for a in guard_advances
            ],
        }
        result.append(row)

    return result


# ── Endpoints ──

@router.get("/report", summary="Cash advance sheet report")
def get_sheet_report(
    date_from: date = Query(...),
    date_to: date = Query(...),
    tab: str = Query("pending", description="approved, rejected, or pending"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Get the cash advance sheet report with all joined data."""
    if tab not in ("approved", "rejected", "pending"):
        raise HTTPException(status_code=400, detail="tab must be: approved, rejected, or pending")

    rows = _build_sheet_data(db, date_from, date_to, tab)

    return {
        "tab": tab,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total": len(rows),
        "employees": rows,
    }


@router.put("/approve/{advance_id}", summary="Admin approves a pending advance")
def approve_advance(
    advance_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin directly approves a pending or ops-approved cash advance."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance not found")

    if advance.status not in ("pending", "ops_approved"):
        raise HTTPException(status_code=400, detail=f"Cannot approve advance with status '{advance.status}'")

    advance.status = "admin_approved"
    advance.admin_id = current_user.user_id
    advance.admin_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return {"message": "Advance approved", "advance_id": advance_id, "status": advance.status}


@router.put("/reject/{advance_id}", summary="Admin rejects a pending advance")
def reject_advance(
    advance_id: str,
    data: ReviewRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin rejects a pending or ops-approved cash advance."""
    advance = db.query(CashAdvance).filter(CashAdvance.advance_id == advance_id).first()
    if not advance:
        raise HTTPException(status_code=404, detail="Cash advance not found")

    if advance.status not in ("pending", "ops_approved"):
        raise HTTPException(status_code=400, detail=f"Cannot reject advance with status '{advance.status}'")

    advance.status = "admin_rejected"
    advance.admin_id = current_user.user_id
    advance.admin_notes = data.notes
    advance.admin_reviewed_at = datetime.now(timezone.utc)
    advance.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(advance)

    return {"message": "Advance rejected", "advance_id": advance_id, "status": advance.status}


@router.put("/update-cells", summary="Batch update editable cells")
def batch_update_cells(
    data: BatchUpdateRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Batch update editable fields (advance amount overrides, deductions, etc.)."""
    updated = 0
    for item in data.updates:
        if item.field == "advance_amount_override":
            # Find the latest pending/approved advance for this user and update
            advance = (
                db.query(CashAdvance)
                .filter(
                    CashAdvance.guard_id == item.user_id,
                    CashAdvance.status.in_(["pending", "ops_approved", "admin_approved"]),
                )
                .order_by(CashAdvance.created_at.desc())
                .first()
            )
            if advance:
                advance.approved_amount = item.value
                advance.updated_at = datetime.now(timezone.utc)
                updated += 1

    db.commit()
    return {"message": f"Updated {updated} cells"}


@router.get("/export-csv", summary="Export cash advance sheet as CSV")
def export_csv(
    date_from: date = Query(...),
    date_to: date = Query(...),
    tab: str = Query("pending"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Export the cash advance sheet as CSV with Arabic headers."""
    rows = _build_sheet_data(db, date_from, date_to, tab)

    headers = [
        "الاكواد", "مسلسل", "التصنيف", "الاسم", "المشرف", "مشروع",
        "تاريخ التعيين", "غياب باذن", "غياب بدون", "اضافى",
        "بدل راحه", "تاخير", "خصم", "راحة",
        "اجازة من السنوي", "اجازة مرضي", "تقييم", "المبلغ",
    ]

    output = io.StringIO()
    # Write BOM for Excel Arabic support
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(headers)

    for r in rows:
        writer.writerow([
            r["employee_code"],
            r["badge_number"],
            r["classification"],
            r["name"],
            r["supervisor_name"],
            r["site_name"],
            r["hire_date"],
            r["excused_absence"],
            r["unexcused_absence"],
            r["overtime_hours"],
            r["rest_allowance"],
            r["late_minutes"],
            r["deductions"],
            r["rest_days"],
            r["annual_leave"],
            r["sick_leave"],
            r["evaluation_score"],
            r["advance_amount"],
        ])

    output.seek(0)
    tab_label = {"approved": "موافق", "rejected": "مرفوض", "pending": "قيد_المراجعة"}.get(tab, tab)
    filename = f"cash_advance_sheet_{tab_label}_{date_from}_{date_to}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
