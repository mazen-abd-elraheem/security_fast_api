"""
SecureTrack - Leader Daily Attendance API
Endpoints for Leaders to record daily attendance for guards at their sites.
"""
import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.site import Site
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.enums import UserRole

router = APIRouter()


# â”€â”€ Schemas â”€â”€

class AttendanceEntryInput(BaseModel):
    employee_id: str
    status: str = Field(..., description="present/absence_excused/absence_unexcused/annual_leave/sick_leave/rest/rest_day_worked")
    late_minutes: int = 0
    overtime_hours: float = 0.0
    overtime_approved_by: Optional[str] = None
    excused_by: Optional[str] = None
    note: Optional[str] = None


class BulkAttendanceInput(BaseModel):
    site_id: str
    entry_date: str  # YYYY-MM-DD
    entries: List[AttendanceEntryInput]


class AttendanceEntryResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: Optional[str] = None
    site_id: str
    entry_date: str
    status: str
    late_minutes: int = 0
    overtime_hours: float = 0.0
    overtime_approved: bool = False
    overtime_approved_by: Optional[str] = None
    excused_by: Optional[str] = None
    note: Optional[str] = None
    locked: bool = False
    entered_by: str


# â”€â”€ Guard list for a site on a given day â”€â”€

@router.get("/sites/{site_id}/guards", summary="List guards at site for attendance")
def get_site_guards_for_attendance(
    site_id: str,
    entry_date: str = Query(..., description="YYYY-MM-DD"),
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """
    Returns all guards rostered at this site on the given date,
    plus any existing attendance entries for the day.
    """
    target_date = date.fromisoformat(entry_date)

    # Get guards rostered at this site on this date
    rosters = (
        db.query(GuardRoster, User)
        .join(Shift, GuardRoster.shift_id == Shift.shift_id)
        .join(User, GuardRoster.guard_id == User.user_id)
        .filter(
            Shift.site_id == site_id,
            GuardRoster.assigned_date == target_date,
        )
        .all()
    )

    guard_ids = [r[1].user_id for r in rosters]

    # Get existing entries for these guards
    existing = {}
    if guard_ids:
        entries = (
            db.query(DailyAttendanceEntry)
            .filter(
                DailyAttendanceEntry.employee_id.in_(guard_ids),
                DailyAttendanceEntry.entry_date == target_date,
                DailyAttendanceEntry.site_id == site_id,
            )
            .all()
        )
        existing = {e.employee_id: e for e in entries}

    result = []
    seen_guards = set()
    for roster, guard in rosters:
        if guard.user_id in seen_guards:
            continue
        seen_guards.add(guard.user_id)
        entry = existing.get(guard.user_id)
        result.append({
            "employee_id": guard.user_id,
            "employee_name": guard.name,
            "employee_code": guard.employee_code,
            "classification": guard.classification,
            "roster_id": roster.roster_id,
            "has_entry": entry is not None,
            "entry": {
                "id": entry.id,
                "status": entry.status,
                "late_minutes": entry.late_minutes,
                "overtime_hours": entry.overtime_hours,
                "overtime_approved": entry.overtime_approved,
                "overtime_approved_by": entry.overtime_approved_by,
                "excused_by": entry.excused_by,
                "note": entry.note,
                "locked": entry.locked,
            } if entry else None,
        })

    # Get site info
    site = db.query(Site).filter(Site.site_id == site_id).first()

    return {
        "site_id": site_id,
        "site_name": site.name if site else "",
        "entry_date": entry_date,
        "guards": result,
        "total": len(result),
        "submitted": sum(1 for g in result if g["has_entry"]),
    }


# â”€â”€ Bulk upsert â”€â”€

@router.post("/bulk", summary="Bulk save daily attendance")
def bulk_save_attendance(
    payload: BulkAttendanceInput,
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """
    Bulk upsert attendance entries for a site on a given date.
    If an entry already exists for (employee_id, entry_date), it is updated.
    Rejects writes to locked days unless caller has override permission.
    """
    target_date = date.fromisoformat(payload.entry_date)
    results = []

    # Deduplicate entries by employee_id — keep last entry per employee
    seen_emp = set()
    unique_entries = []
    for record in reversed(payload.entries):
        if record.employee_id not in seen_emp:
            seen_emp.add(record.employee_id)
            unique_entries.append(record)
    unique_entries.reverse()

    for record in unique_entries:
        # Check if entry exists
        existing = (
            db.query(DailyAttendanceEntry)
            .filter(
                DailyAttendanceEntry.employee_id == record.employee_id,
                DailyAttendanceEntry.entry_date == target_date,
            )
            .first()
        )

        if existing:
            # Check lock
            if existing.locked and current_user.role not in ('admin', 'supervisor', 'hr'):
                results.append({"employee_id": record.employee_id, "status": "locked", "error": "Day is locked"})
                continue

            if existing.locked:
                # Override â€” log it
                existing.override_reason = f"Edited after lock by {current_user.name}"
                existing.overridden_by = current_user.user_id
                existing.overridden_at = datetime.now(timezone.utc)

            existing.status = record.status
            existing.late_minutes = record.late_minutes
            existing.overtime_hours = record.overtime_hours
            existing.overtime_approved_by = record.overtime_approved_by
            existing.excused_by = record.excused_by
            existing.note = record.note
            existing.site_id = payload.site_id
            results.append({"employee_id": record.employee_id, "status": "updated", "id": existing.id})
        else:
            entry = DailyAttendanceEntry(
                id=str(uuid.uuid4()),
                employee_id=record.employee_id,
                site_id=payload.site_id,
                entry_date=target_date,
                status=record.status,
                late_minutes=record.late_minutes,
                overtime_hours=record.overtime_hours,
                overtime_approved_by=record.overtime_approved_by,
                excused_by=record.excused_by,
                note=record.note,
                entered_by=current_user.user_id,
            )
            db.add(entry)
            results.append({"employee_id": record.employee_id, "status": "created", "id": entry.id})

    db.commit()

    return {
        "entry_date": payload.entry_date,
        "site_id": payload.site_id,
        "results": results,
        "total": len(results),
    }


# â”€â”€ Lock a day â”€â”€

@router.post("/lock-day", summary="Lock entries for a day")
def lock_day(
    site_id: str = Query(...),
    entry_date: str = Query(...),
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN, UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Lock all entries for a site on a given day."""
    target_date = date.fromisoformat(entry_date)
    now = datetime.now(timezone.utc)

    count = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.site_id == site_id,
            DailyAttendanceEntry.entry_date == target_date,
            DailyAttendanceEntry.locked == False,
        )
        .update({"locked": True, "locked_at": now})
    )
    db.commit()

    return {"locked_count": count, "entry_date": entry_date, "site_id": site_id}


# â”€â”€ Get summary for a month (for accountant grid) â”€â”€

@router.get("/monthly-summary", summary="Monthly attendance summary for payroll")
def get_monthly_summary(
    year: int = Query(...),
    month: int = Query(...),
    site_id: Optional[str] = None,
    role_filter: Optional[str] = Query(None, description="guard, outdoor, supervisor"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """
    Returns ALL active employees with monthly attendance totals.
    Includes guards, outdoor, and supervisors.
    Enriches with site name, shift time, classification from role.
    """
    from calendar import monthrange
    from app.models.site import Site

    _, days_in_month = monthrange(year, month)
    date_from = date(year, month, 1)
    date_to = date(year, month, days_in_month)

    # 1) Get active employees (guards + outdoor + supervisors)
    roles = ["guard", "outdoor", "lady"]
    if role_filter:
        roles = [role_filter]
    else:
        roles = ["guard", "outdoor", "supervisor", "lady"]

    all_employees = db.query(User).filter(
        User.role.in_(roles),
        User.is_active == True,
        User.status != "terminated",
    ).all()

    # 2) Get daily attendance entries for this month
    entry_query = db.query(DailyAttendanceEntry).filter(
        DailyAttendanceEntry.entry_date >= date_from,
        DailyAttendanceEntry.entry_date <= date_to,
    )
    if site_id:
        entry_query = entry_query.filter(DailyAttendanceEntry.site_id == site_id)
    all_entries = entry_query.all()

    # Group entries by employee
    emp_entries = {}
    for e in all_entries:
        emp_entries.setdefault(e.employee_id, []).append(e)

    # 3) Get holidays for this month
    from app.models.accountant_models import Holiday, EmployeeBonus
    holidays = db.query(Holiday).filter(
        Holiday.date >= date_from,
        Holiday.date <= date_to,
    ).all()
    holiday_days = {h.date.day for h in holidays}

    # 4) Get bonuses for this month
    bonuses = db.query(EmployeeBonus).filter(
        EmployeeBonus.year == year,
        EmployeeBonus.month == month,
    ).all()
    emp_bonuses = {}
    for b in bonuses:
        emp_bonuses.setdefault(b.employee_id, []).append(b)

    # 5) Get roster -> shift -> site mapping for each employee
    emp_site_info = {}
    for emp in all_employees:
        roster = db.query(GuardRoster).filter(
            GuardRoster.guard_id == emp.user_id,
        ).order_by(GuardRoster.assigned_date.desc()).first()

        site_name = ""
        shift_time = "none"
        supervisor_name = ""

        if roster:
            shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
            if shift:
                site = db.query(Site).filter(Site.site_id == shift.site_id).first()
                if site:
                    site_name = site.name or ""
                if shift.start_time:
                    shift_time = "\u0635" if shift.start_time.hour < 12 else "\u0645"

        emp_site_info[emp.user_id] = {
            "site_name": site_name,
            "shift_time": shift_time,
            "supervisor_name": supervisor_name,
        }

    # 6) Build rows
    role_labels = {"guard": "\u062d\u0627\u0631\u0633", "outdoor": "\u062e\u0627\u0631\u062c\u064a", "supervisor": "\u0645\u0634\u0631\u0641"}
    rows = []
    for idx, emp in enumerate(all_employees, 1):
        entries = emp_entries.get(emp.user_id, [])
        site_info = emp_site_info.get(emp.user_id, {})

        # Count attendance totals
        total_present = 0
        total_absent_excused = 0
        total_absent_unexcused = 0
        total_rest = 0
        total_annual_leave = 0
        total_sick_leave = 0
        total_late = 0
        total_overtime = 0.0
        total_rest_allowance = 0

        for e in entries:
            if e.status == "present":
                total_present += 1
            elif e.status == "absence_excused":
                total_absent_excused += 1
            elif e.status == "absence_unexcused":
                total_absent_unexcused += 1
            elif e.status == "rest":
                total_rest += 1
            elif e.status == "annual_leave":
                total_annual_leave += 1
            elif e.status == "sick_leave":
                total_sick_leave += 1
            elif e.status == "rest_day_worked":
                total_rest_allowance += 1
                total_present += 1

            if e.late_minutes > 0:
                total_late += 1
            if e.overtime_hours > 0 and e.overtime_approved:
                total_overtime += e.overtime_hours

        total_holidays = len(holiday_days)
        total_off = total_rest + total_holidays + total_annual_leave + total_sick_leave
        total_absent = total_absent_excused + total_absent_unexcused
        total_days = total_present + total_absent + total_off
        working_days = total_present

        # Salary calculations
        daily_rate = float(emp.daily_rate or 0)
        base_salary = float(emp.base_salary or 0)
        operational_salary = daily_rate * working_days if daily_rate > 0 else 0
        gross_salary = operational_salary if operational_salary > 0 else base_salary

        # Bonuses
        emp_bonus_list = emp_bonuses.get(emp.user_id, [])
        total_bonus = sum(b.amount for b in emp_bonus_list)

        net_salary = gross_salary + total_bonus

        # Missing days (not yet submitted by leader)
        entered_days = {e.entry_date.day for e in entries}
        missing_days = [d for d in range(1, days_in_month + 1) if d not in entered_days and d not in holiday_days]

        classification = emp.classification or role_labels.get(emp.role, emp.role)

        rows.append({
            "employee_id": emp.user_id,
            "serial_no": idx,
            "employee_code": emp.employee_code or emp.badge_number or "",
            "name": emp.name,
            "employee_name": emp.name,
            "role": emp.role,
            "classification": classification,
            "shift_time": site_info.get("shift_time", "none"),
            "shift_type": getattr(emp, "shift_type", "") or site_info.get("shift_time", "none"),
            "work_schedule": getattr(emp, "shift_type", "") or site_info.get("shift_time", "none"),
            "supervisor_name": site_info.get("supervisor_name", ""),
            "site_name": site_info.get("site_name", ""),
            "hire_date": str(emp.hire_date.strftime("%Y-%m-%d") if emp.hire_date else ""),
            "insurance_status": emp.insurance_status or "none",
            "bank_account": getattr(emp, "bank_account", "") or "",
            "transfer_name": getattr(emp, "transfer_name", "") or "",
            "transfer_method": getattr(emp, "transfer_method", "") or "",
            "uniform_status": "",
            "daily_rate": daily_rate,
            "base_salary": base_salary,
            # Monthly totals
            "total_present": total_present,
            "total_absent_excused": total_absent_excused,
            "total_absent_unexcused": total_absent_unexcused,
            "total_absent": total_absent,
            "total_rest": total_rest,
            "total_rest_allowance": total_rest_allowance,
            "total_annual_leave": total_annual_leave,
            "total_sick_leave": total_sick_leave,
            "total_holidays": total_holidays,
            "total_off": total_off,
            "total_late": total_late,
            "total_overtime": total_overtime,
            "total_days": total_days,
            "working_days": working_days,
            "missing_days_count": len(missing_days),
            # Salary
            "operational_working_days": working_days,
            "operational_salary": operational_salary,
            "gross_salary": gross_salary,
            "total_bonus": total_bonus,
            "net_salary": net_salary,
        })

    # Get all unique sites for filter
    all_sites = list({r["site_name"] for r in rows if r["site_name"]})

    return {
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "total_employees": len(rows),
        "total_gross": sum(r["gross_salary"] for r in rows),
        "total_net": sum(r["net_salary"] for r in rows),
        "holidays": [{"name": h.name, "date": str(h.date), "day": h.date.day} for h in holidays],
        "sites": sorted(all_sites),
        "rows": rows,
    }

