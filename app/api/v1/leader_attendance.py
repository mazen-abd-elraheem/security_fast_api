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
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN)),
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
    for roster, guard in rosters:
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
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Bulk upsert attendance entries for a site on a given date.
    If an entry already exists for (employee_id, entry_date), it is updated.
    Rejects writes to locked days unless caller has override permission.
    """
    target_date = date.fromisoformat(payload.entry_date)
    results = []

    for record in payload.entries:
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
    current_user: User = Depends(require_role(UserRole.LEADER, UserRole.ADMIN)),
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
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """
    Aggregates daily_attendance_entries into weekly blocks for the payroll grid.
    Week 1: days 1-7, Week 2: 8-14, Week 3: 15-21, Week 4: 22-end.
    Returns data ready for the accountant Excel-style view.
    """
    from calendar import monthrange

    _, days_in_month = monthrange(year, month)
    date_from = date(year, month, 1)
    date_to = date(year, month, days_in_month)
    week_ranges = [(1, 7), (8, 14), (15, 21), (22, days_in_month)]

    query = db.query(DailyAttendanceEntry).filter(
        DailyAttendanceEntry.entry_date >= date_from,
        DailyAttendanceEntry.entry_date <= date_to,
    )
    if site_id:
        query = query.filter(DailyAttendanceEntry.site_id == site_id)

    all_entries = query.all()

    # Group by employee
    emp_entries = {}
    for e in all_entries:
        emp_entries.setdefault(e.employee_id, []).append(e)

    # Get employee info
    emp_ids = list(emp_entries.keys())
    employees = {}
    if emp_ids:
        for u in db.query(User).filter(User.user_id.in_(emp_ids)).all():
            employees[u.user_id] = u

    rows = []
    for emp_id, entries in emp_entries.items():
        emp = employees.get(emp_id)
        if not emp:
            continue

        weekly = []
        for w_start, w_end in week_ranges:
            w = {
                "absent_excused": 0, "absent_unexcused": 0, "overtime": 0.0,
                "rest_allowance": 0, "late": 0, "deduction": 0.0,
                "rest": 0, "annual_leave": 0, "sick_leave": 0,
            }
            for e in entries:
                day = e.entry_date.day
                if day < w_start or day > w_end:
                    continue
                if e.status == "absence_excused":
                    w["absent_excused"] += 1
                elif e.status == "absence_unexcused":
                    w["absent_unexcused"] += 1
                elif e.status == "annual_leave":
                    w["annual_leave"] += 1
                elif e.status == "sick_leave":
                    w["sick_leave"] += 1
                elif e.status == "rest":
                    w["rest"] += 1
                elif e.status == "rest_day_worked":
                    w["rest_allowance"] += 1
                if e.late_minutes > 0:
                    w["late"] += 1
                if e.overtime_hours > 0 and e.overtime_approved:
                    w["overtime"] += e.overtime_hours
            weekly.append(w)

        # Check for missing days (no entry at all)
        entered_days = {e.entry_date.day for e in entries}
        missing_days = [d for d in range(1, days_in_month + 1) if d not in entered_days]

        working_days = sum(1 for e in entries if e.status in ("present", "rest_day_worked"))
        working_days += sum(1 for e in entries if e.status == "present" and e.late_minutes > 0)  # late still counts

        rows.append({
            "employee_id": emp_id,
            "employee_code": emp.employee_code or "",
            "employee_name": emp.name,
            "classification": emp.classification or "",
            "shift_type": getattr(emp, "shift_type", "") or "",
            "insurance_status": emp.insurance_status or "",
            "hire_date": str(emp.hire_date or ""),
            "weekly": weekly,
            "total_entries": len(entries),
            "missing_days": missing_days,
            "working_days": working_days,
        })

    return {
        "year": year,
        "month": month,
        "total_employees": len(rows),
        "rows": rows,
    }


