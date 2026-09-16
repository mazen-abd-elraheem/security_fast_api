"""
SecureTrack Platform — Deduction Sheet Routes
Aggregates all deduction sources for employees into a single report:
  - Attendance-based: absences (excused/unexcused), lateness
  - Disciplinary: supervisor deductions, warnings
  - Complaints: complaint-based deductions
  - Notice period: early termination penalties
"""
import uuid
import io
import csv
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.disciplinary_action import DisciplinaryAction
from app.models.complaint import Complaint
from app.models.deduction_rule import DeductionRule
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.supervisor_route import SupervisorRoute
from app.enums import UserRole

router = APIRouter()

SHEET_ROLES = ["guard", "outdoor", "supervisor", "lady", "leader"]

ROLE_ARABIC_MAP = {
    "guard": "حارس",
    "outdoor": "خارجي",
    "supervisor": "مشرف",
    "lady": "سيدة",
    "leader": "قائد",
}


@router.get("/report", summary="Get deduction sheet report")
def get_deduction_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """
    Build a comprehensive deduction report aggregating:
    - DailyAttendanceEntry: absences, late minutes
    - DisciplinaryAction: supervisor deductions (active only)
    - Complaint: resolved complaints
    - DeductionRule: rates for calculating monetary deductions
    """

    # 1. Get all active employees with matching roles
    users = (
        db.query(User)
        .filter(User.is_active == True, User.role.in_(SHEET_ROLES))
        .order_by(User.name)
        .all()
    )
    if not users:
        return {"employees": [], "date_from": date_from.isoformat(), "date_to": date_to.isoformat()}

    user_ids = [u.user_id for u in users]
    user_map = {u.user_id: u for u in users}

    # 2. Bulk-load attendance entries
    entries = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id.in_(user_ids),
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        )
        .all()
    )
    entries_by_user: dict[str, list] = {uid: [] for uid in user_ids}
    for e in entries:
        entries_by_user[e.employee_id].append(e)

    # 3. Bulk-load disciplinary actions (active deductions within date range)
    disc_actions = (
        db.query(DisciplinaryAction)
        .filter(
            DisciplinaryAction.guard_id.in_(user_ids),
            DisciplinaryAction.action_type == "deduction",
            DisciplinaryAction.status == "active",
            DisciplinaryAction.created_at >= datetime.combine(date_from, datetime.min.time()),
            DisciplinaryAction.created_at <= datetime.combine(date_to, datetime.max.time()),
        )
        .all()
    )
    disc_by_user: dict[str, list] = {uid: [] for uid in user_ids}
    for d in disc_actions:
        disc_by_user[d.guard_id].append(d)

    # 4. Bulk-load complaints (resolved with deductions)
    complaints = (
        db.query(Complaint)
        .filter(
            Complaint.guard_id.in_(user_ids),
            Complaint.status.in_(["resolved", "hr_resolved"]),
            Complaint.created_at >= datetime.combine(date_from, datetime.min.time()),
            Complaint.created_at <= datetime.combine(date_to, datetime.max.time()),
        )
        .all()
    )
    complaints_by_user: dict[str, list] = {uid: [] for uid in user_ids}
    for c in complaints:
        complaints_by_user[c.guard_id].append(c)

    # 5. Load deduction rules for rate calculation
    rules = db.query(DeductionRule).filter(DeductionRule.is_active == True).all()
    rule_map = {r.rule_type: r for r in rules}
    absent_rule = rule_map.get("absent")
    late_rule = rule_map.get("late")

    # 6. Bulk-load latest roster → shift → site for each user
    # Get the latest roster per user within date range
    latest_roster_sub = (
        db.query(
            GuardRoster.guard_id,
            func.max(GuardRoster.assigned_date).label("max_date"),
        )
        .filter(
            GuardRoster.guard_id.in_(user_ids),
            GuardRoster.assigned_date >= date_from,
            GuardRoster.assigned_date <= date_to,
        )
        .group_by(GuardRoster.guard_id)
        .subquery()
    )
    latest_rosters = (
        db.query(GuardRoster)
        .join(latest_roster_sub, and_(
            GuardRoster.guard_id == latest_roster_sub.c.guard_id,
            GuardRoster.assigned_date == latest_roster_sub.c.max_date,
        ))
        .all()
    )
    roster_by_user = {r.guard_id: r for r in latest_rosters}

    # Load shifts and sites
    shift_ids = list(set(r.shift_id for r in latest_rosters if r.shift_id))
    shifts = db.query(Shift).filter(Shift.shift_id.in_(shift_ids)).all() if shift_ids else []
    shift_map = {s.shift_id: s for s in shifts}

    site_ids = list(set(s.site_id for s in shifts if s.site_id))
    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all() if site_ids else []
    site_map = {s.site_id: s for s in sites}

    # 7. Bulk-load supervisor names per site
    site_supervisor_map: dict[str, str] = {}
    if site_ids:
        sup_route_sub = (
            db.query(
                SupervisorRoute.site_id,
                func.max(SupervisorRoute.assigned_date).label("max_date"),
            )
            .filter(SupervisorRoute.site_id.in_(site_ids))
            .group_by(SupervisorRoute.site_id)
            .subquery()
        )
        sup_routes = (
            db.query(SupervisorRoute)
            .join(sup_route_sub, and_(
                SupervisorRoute.site_id == sup_route_sub.c.site_id,
                SupervisorRoute.assigned_date == sup_route_sub.c.max_date,
            ))
            .all()
        )
        sup_user_ids = [sr.supervisor_id for sr in sup_routes]
        if sup_user_ids:
            sup_users = db.query(User).filter(User.user_id.in_(sup_user_ids)).all()
            sup_name_map = {u.user_id: u.name for u in sup_users}
            for sr in sup_routes:
                name = sup_name_map.get(sr.supervisor_id)
                if name:
                    site_supervisor_map[sr.site_id] = name

    # 8. Build employee rows
    employees = []
    for user in users:
        uid = user.user_id
        user_entries = entries_by_user.get(uid, [])
        user_disc = disc_by_user.get(uid, [])
        user_complaints = complaints_by_user.get(uid, [])

        # Attendance deductions
        absent_excused = sum(1 for e in user_entries if e.status == "absence_excused")
        absent_unexcused = sum(1 for e in user_entries if e.status == "absence_unexcused")
        total_late_minutes = sum(e.late_minutes for e in user_entries if e.late_minutes > 0)

        # Disciplinary deductions
        disc_days = sum(d.deduction_days or 0 for d in user_disc)
        disc_amount = sum(d.deduction_amount or 0.0 for d in user_disc)

        # Build deduction reasons
        reasons = []
        if absent_unexcused > 0:
            reasons.append(f"غياب بدون إذن ({absent_unexcused} يوم)")
        if absent_excused > 0:
            reasons.append(f"غياب بإذن ({absent_excused} يوم)")
        if total_late_minutes > 0:
            reasons.append(f"تأخير ({total_late_minutes} دقيقة)")
        for d in user_disc:
            reasons.append(f"إجراء تأديبي: {d.reason or 'خصم'}")
        for c in user_complaints:
            reasons.append(f"شكوى: {c.subject}")

        # Calculate total deduction days
        deduction_days = absent_unexcused + disc_days

        # Calculate total deduction amount
        total_deduction = disc_amount
        if absent_rule:
            if absent_rule.is_days_multiplier and user.daily_rate:
                total_deduction += absent_unexcused * user.daily_rate * absent_rule.amount
            else:
                total_deduction += absent_unexcused * absent_rule.amount
        if late_rule:
            grace = late_rule.threshold_minutes or 0
            late_billable = max(0, total_late_minutes - grace * sum(
                1 for e in user_entries if e.late_minutes > grace
            )) if not late_rule.is_per_minute else total_late_minutes
            if late_rule.is_per_minute:
                total_deduction += late_billable * late_rule.amount
            else:
                late_incidents = sum(1 for e in user_entries if e.late_minutes > (late_rule.threshold_minutes or 0))
                total_deduction += late_incidents * late_rule.amount

        total_deduction = round(total_deduction, 2)

        # Shift/Site info
        roster = roster_by_user.get(uid)
        shift_label = ""
        site_name = ""
        supervisor_name = ""
        if roster:
            shift = shift_map.get(roster.shift_id)
            if shift:
                shift_label = shift.label or ""
                if shift.start_time and shift.end_time:
                    shift_label = f"{shift.label or ''} ({shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')})"
                site = site_map.get(shift.site_id)
                if site:
                    site_name = site.name or ""
                    supervisor_name = site_supervisor_map.get(shift.site_id, "")

        employees.append({
            "user_id": uid,
            "badge_number": user.employee_code or user.badge_number or "",
            "name": user.name or "",
            "classification": user.classification or ROLE_ARABIC_MAP.get(user.role, user.role or ""),
            "shift_label": shift_label,
            "supervisor_name": supervisor_name,
            "site_name": site_name,
            "deduction_days": deduction_days,
            "deduction_reasons": " | ".join(reasons) if reasons else "لا يوجد",
            "total_deduction": total_deduction,
            "absent_excused": absent_excused,
            "absent_unexcused": absent_unexcused,
            "late_minutes": total_late_minutes,
            "disc_days": disc_days,
            "disc_amount": disc_amount,
        })

    return {
        "employees": employees,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total": len(employees),
    }


@router.get("/export-csv", summary="Export deduction sheet as CSV")
def export_deduction_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export the deduction sheet as a CSV file."""
    report = get_deduction_report(date_from=date_from, date_to=date_to, current_user=current_user, db=db)
    employees = report["employees"]

    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM for Arabic
    writer = csv.writer(output)

    headers = [
        "الكود",
        "الاسم",
        "التصنيف",
        "الشيفت",
        "اسم المشرف",
        "الموقع",
        "ايام الخصم",
        "اسباب الخصم",
        "اجمالي الخصم",
    ]
    writer.writerow(headers)

    for emp in employees:
        writer.writerow([
            emp["badge_number"],
            emp["name"],
            emp["classification"],
            emp["shift_label"],
            emp["supervisor_name"],
            emp["site_name"],
            emp["deduction_days"],
            emp["deduction_reasons"],
            emp["total_deduction"],
        ])

    output.seek(0)
    filename = f"deduction_sheet_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
