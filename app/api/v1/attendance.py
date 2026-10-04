"""
SecureTrack Platform — Attendance Routes
All attendance now based on DailyAttendanceEntry (not AttendanceLog).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import date, datetime, timezone
from typing import Optional
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.daily_attendance_entry import DailyAttendanceEntry
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.enums import UserRole

router = APIRouter()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _entry_to_dict(e: DailyAttendanceEntry, guard: User | None = None, site: Site | None = None) -> dict:
    return {
        "entry_id": e.id,
        "employee_id": e.employee_id,
        "employee_name": guard.name if guard else None,
        "site_id": e.site_id,
        "site_name": site.name if site else None,
        "entry_date": e.entry_date.isoformat() if e.entry_date else None,
        "status": e.status,
        "late_minutes": e.late_minutes,
        "overtime_hours": e.overtime_hours,
        "overtime_approved": e.overtime_approved,
        "overtime_approved_by": e.overtime_approved_by,
        "excused_by": e.excused_by,
        "advance_amount": e.advance_amount,
        "note": e.note,
        "replaced_by_id": e.replaced_by_id,
        "locked": e.locked,
        "entered_by": e.entered_by,
        "roster_id": e.roster_id,
        "shift_id": e.shift_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


# ─────────────────────────────────────────────
# Supervisor dashboard: get guards with their daily status
# ─────────────────────────────────────────────

@router.get("/supervisor/dashboard", summary="Supervisor attendance dashboard for assigned sites")
def supervisor_attendance_dashboard(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER, UserRole.HR, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Returns all guards rostered at the supervisor's assigned sites today,
    along with their DailyAttendanceEntry status.
    """
    from app.models.supervisor_route import SupervisorRoute
    from sqlalchemy.orm import joinedload

    if target_date is None:
        target_date = date.today()

    routes = (
        db.query(SupervisorRoute)
        .filter(SupervisorRoute.supervisor_id == current_user.user_id)
        .filter(SupervisorRoute.assigned_date == target_date)
        .all()
    )
    site_ids = list({r.site_id for r in routes})
    if not site_ids:
        return {"sites": [], "total_guards": 0, "total_present": 0, "date": target_date.isoformat()}

    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all()
    site_map = {s.site_id: s for s in sites}

    # Determine which shifts this supervisor is assigned to
    assigned_shift_ids_by_site: dict[str, set] = {sid: set() for sid in site_ids}
    has_specific_shifts: dict[str, bool] = {sid: False for sid in site_ids}
    for r in routes:
        if r.shift_id:
            assigned_shift_ids_by_site[r.site_id].add(r.shift_id)
            has_specific_shifts[r.site_id] = True

    all_site_shifts = db.query(Shift).filter(Shift.site_id.in_(site_ids), Shift.is_active == True).all()
    shifts = []
    for s in all_site_shifts:
        if has_specific_shifts.get(s.site_id, False):
            if s.shift_id in assigned_shift_ids_by_site.get(s.site_id, set()):
                shifts.append(s)
        else:
            shifts.append(s)

    shift_map = {s.shift_id: s for s in shifts}
    site_shift_ids: dict[str, list] = {sid: [] for sid in site_ids}
    for s in shifts:
        site_shift_ids[s.site_id].append(s.shift_id)

    all_shift_ids = [s.shift_id for s in shifts]
    rosters = []
    if all_shift_ids:
        rosters = (
            db.query(GuardRoster)
            .options(joinedload(GuardRoster.guard))
            .filter(
                GuardRoster.shift_id.in_(all_shift_ids),
                GuardRoster.assigned_date == target_date,
                GuardRoster.status != "canceled",
            )
            .all()
        )

    # Load DailyAttendanceEntries for all guards at these sites today
    guard_ids = list({r.guard_id for r in rosters if r.guard_id})
    entries_map: dict[str, DailyAttendanceEntry] = {}
    if guard_ids:
        entries = (
            db.query(DailyAttendanceEntry)
            .filter(
                DailyAttendanceEntry.employee_id.in_(guard_ids),
                DailyAttendanceEntry.entry_date == target_date,
                DailyAttendanceEntry.site_id.in_(site_ids),
            )
            .all()
        )
        entries_map = {e.employee_id: e for e in entries}

    # Group rosters by site
    roster_by_site: dict[str, list] = {sid: [] for sid in site_ids}
    for r in rosters:
        shift_obj = shift_map.get(r.shift_id)
        if shift_obj:
            roster_by_site[shift_obj.site_id].append(r)

    sites_data = []
    total_guards = 0
    total_present = 0

    for site_id in site_ids:
        site = site_map.get(site_id)
        if not site:
            continue

        guards = []
        site_present = 0
        seen_guard_ids: set = set()

        for roster in roster_by_site[site_id]:
            guard = roster.guard
            shift = shift_map.get(roster.shift_id)

            if guard and guard.user_id in seen_guard_ids:
                continue
            if guard:
                seen_guard_ids.add(guard.user_id)

            entry = entries_map.get(guard.user_id) if guard else None
            att_status = entry.status if entry else "not_recorded"
            if att_status in ("present", "rest_day_worked"):
                site_present += 1

            guards.append({
                "guard_id": guard.user_id if guard else None,
                "guard_name": guard.name if guard else "Unknown",
                "guard_code": guard.employee_code if guard else None,
                "roster_id": roster.roster_id,
                "shift_id": roster.shift_id,
                "shift_label": shift.label if shift else None,
                "shift_time": f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}" if shift else None,
                "status": att_status,
                "entry_id": entry.id if entry else None,
                "late_minutes": entry.late_minutes if entry else 0,
                "overtime_hours": entry.overtime_hours if entry else 0,
                "note": entry.note if entry else None,
                "locked": entry.locked if entry else False,
                "created_at": entry.created_at.isoformat() if entry and entry.created_at else None,
            })

        total_guards += len(guards)
        total_present += site_present
        sites_data.append({
            "site_id": site_id,
            "site_name": site.name,
            "guards": guards,
            "total": len(guards),
            "present": site_present,
        })

    return {
        "sites": sites_data,
        "total_guards": total_guards,
        "total_present": total_present,
        "date": target_date.isoformat(),
    }


# ─────────────────────────────────────────────
# Guard auto check-in via GPS → writes DailyAttendanceEntry
# ─────────────────────────────────────────────

from app.services.geo_service import GeoService

@router.post("/checkin", status_code=200, summary="Guard auto check-in via GPS")
def guard_checkin(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_role(UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Auto check-in: find the guard's roster for today, verify geofence,
    then create or update a DailyAttendanceEntry as 'present'.
    """
    import logging
    logger = logging.getLogger("securetrack.checkin")

    today = date.today()
    logger.info(f"[CHECKIN] User={current_user.user_id} ({current_user.name}), lat={latitude}, lng={longitude}, today={today}")

    roster = (
        db.query(GuardRoster)
        .filter(GuardRoster.guard_id == current_user.user_id)
        .filter(GuardRoster.assigned_date == today)
        .filter(GuardRoster.status != "canceled")
        .first()
    )
    if not roster:
        return {"status": "no_assignment", "detail": f"No shift assigned for today ({today})"}

    # Check if already checked in
    existing = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.employee_id == current_user.user_id,
            DailyAttendanceEntry.entry_date == today,
            DailyAttendanceEntry.roster_id == roster.roster_id,
        )
        .first()
    )
    if existing and existing.status in ("present", "rest_day_worked"):
        return {"status": "already_checked_in", "detail": "Already checked in for this shift"}

    shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
    if not shift:
        return {"status": "error", "detail": "Shift not found"}

    site = db.query(Site).filter(Site.site_id == shift.site_id).first()
    if not site:
        return {"status": "error", "detail": "Site not found"}

    distance = GeoService.haversine_distance_meters(latitude, longitude, site.latitude, site.longitude)
    logger.info(f"[CHECKIN] Site={site.name}, distance={int(distance)}m, radius={site.radius_meters}m")

    if distance > site.radius_meters:
        return {
            "status": "out_of_range",
            "detail": f"You are {int(distance)}m away. Must be within {site.radius_meters}m.",
            "distance_meters": int(distance),
        }

    now = datetime.now(timezone.utc)

    if existing:
        existing.status = "present"
        existing.note = f"Auto check-in at {int(distance)}m from site center"
        existing.updated_at = now
        db.commit()
        entry_id = existing.id
    else:
        entry = DailyAttendanceEntry(
            id=str(uuid.uuid4()),
            employee_id=current_user.user_id,
            site_id=site.site_id,
            roster_id=roster.roster_id,
            shift_id=roster.shift_id,
            entry_date=today,
            status="present",
            late_minutes=0,
            overtime_hours=0.0,
            entered_by=current_user.user_id,
            note=f"Auto check-in at {int(distance)}m from site center",
        )
        db.add(entry)
        db.commit()
        entry_id = entry.id

    logger.info(f"[CHECKIN] SUCCESS — entry_id={entry_id}, distance={int(distance)}m")
    return {
        "status": "checked_in",
        "detail": f"Checked in to {site.name}",
        "site_name": site.name,
        "distance_meters": int(distance),
        "recorded_at": now.isoformat(),
    }


@router.get("/checkin/debug", status_code=200, summary="Debug guard check-in status")
def debug_checkin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Debug endpoint: shows what the checkin logic would see for this user."""
    today = date.today()
    rosters = db.query(GuardRoster).filter(
        GuardRoster.guard_id == current_user.user_id,
        GuardRoster.status != "canceled",
    ).all()

    roster_today = [r for r in rosters if r.assigned_date == today]

    result = {
        "user_id": current_user.user_id,
        "name": current_user.name,
        "role": current_user.role.value if hasattr(current_user.role, 'value') else current_user.role,
        "server_today": str(today),
        "total_rosters": len(rosters),
        "rosters_today": len(roster_today),
        "all_roster_dates": [str(r.assigned_date) for r in rosters],
    }

    if roster_today:
        r = roster_today[0]
        shift = db.query(Shift).filter(Shift.shift_id == r.shift_id).first()
        site = db.query(Site).filter(Site.site_id == shift.site_id).first() if shift else None
        existing_entry = db.query(DailyAttendanceEntry).filter(
            DailyAttendanceEntry.employee_id == current_user.user_id,
            DailyAttendanceEntry.entry_date == today,
            DailyAttendanceEntry.roster_id == r.roster_id,
        ).first()

        result["roster_id"] = r.roster_id
        result["shift_id"] = r.shift_id
        result["shift_label"] = shift.label if shift else None
        result["site_name"] = site.name if site else None
        result["site_lat"] = site.latitude if site else None
        result["site_lng"] = site.longitude if site else None
        result["site_radius"] = site.radius_meters if site else None
        result["already_checked_in"] = existing_entry is not None and existing_entry.status in ("present", "rest_day_worked")
        if existing_entry:
            result["existing_entry_id"] = existing_entry.id
            result["existing_entry_status"] = existing_entry.status

        if site and current_user.latitude and current_user.longitude:
            dist = GeoService.haversine_distance_meters(current_user.latitude, current_user.longitude, site.latitude, site.longitude)
            result["stored_location_distance_m"] = int(dist)
            result["within_geofence"] = dist <= site.radius_meters

    return result


# ─────────────────────────────────────────────
# Guard / Supervisor attendance history
# ─────────────────────────────────────────────

@router.get("/guard/{guard_id}", summary="Guard attendance history")
def get_guard_attendance(
    guard_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get DailyAttendanceEntry history for a guard. Guards can only view their own."""
    user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if user_role == "guard" and current_user.user_id != guard_id:
        raise HTTPException(status_code=403, detail="Guards can only view their own attendance")

    guard = db.query(User).filter(User.user_id == guard_id).first()

    q = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.employee_id == guard_id)
    if date_from:
        q = q.filter(DailyAttendanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(DailyAttendanceEntry.entry_date <= date_to)
    entries = q.order_by(DailyAttendanceEntry.entry_date.desc()).all()

    items = []
    for e in entries:
        site = db.query(Site).filter(Site.site_id == e.site_id).first()
        items.append(_entry_to_dict(e, guard=guard, site=site))

    return {"records": items, "total": len(items)}


@router.get("/my", summary="Get my recorded attendance")
def get_my_attendance(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER, UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """Get DailyAttendanceEntries recorded for/by the current user."""
    q = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.employee_id == current_user.user_id)
    if target_date:
        q = q.filter(DailyAttendanceEntry.entry_date == target_date)
    entries = q.order_by(DailyAttendanceEntry.entry_date.desc()).all()

    items = []
    for e in entries:
        site = db.query(Site).filter(Site.site_id == e.site_id).first()
        items.append(_entry_to_dict(e, site=site))

    return {"records": items, "total": len(items)}


# ─────────────────────────────────────────────
# Daily Summary for Admin — read from DailyAttendanceEntry
# ─────────────────────────────────────────────

@router.get("/daily-summary", summary="Supervisor attendance daily summary")
def get_daily_summary(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: Optional[str] = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Get all daily attendance entries grouped by site and date."""
    q = (
        db.query(DailyAttendanceEntry)
        .filter(
            DailyAttendanceEntry.entry_date >= date_from,
            DailyAttendanceEntry.entry_date <= date_to,
        )
    )
    if site_id:
        q = q.filter(DailyAttendanceEntry.site_id == site_id)
    entries = q.all()

    # Pre-fetch users and sites
    emp_ids = {e.employee_id for e in entries}
    s_ids = {e.site_id for e in entries}
    by_ids = {e.entered_by for e in entries}

    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(emp_ids)).all()}
    sites_map = {s.site_id: s for s in db.query(Site).filter(Site.site_id.in_(s_ids)).all()}
    entered_by_map = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(by_ids)).all()}

    sites_data: dict[str, dict] = {}
    for e in entries:
        guard = users.get(e.employee_id)
        site = sites_map.get(e.site_id)
        entered_by_user = entered_by_map.get(e.entered_by)
        site_name = site.name if site else "Unknown"
        date_str = e.entry_date.isoformat()
        key = f"{date_str}_{e.site_id}"

        if key not in sites_data:
            sites_data[key] = {
                "date": date_str,
                "site_id": e.site_id,
                "site_name": site_name,
                "supervisor_name": entered_by_user.name if entered_by_user else "Unknown",
                "guards": [],
                "total_present": 0,
                "total_absent": 0,
                "total_late": 0,
            }

        sites_data[key]["guards"].append({
            "entry_id": e.id,
            "name": guard.name if guard else "Unknown",
            "badge_number": guard.badge_number if guard else "",
            "employee_code": guard.employee_code if guard else "",
            "classification": guard.classification if guard else "",
            "status": e.status,
            "late_minutes": e.late_minutes,
            "overtime_hours": e.overtime_hours,
            "note": e.note or "",
            "locked": e.locked,
            "created_at": e.created_at.isoformat() if e.created_at else "",
        })

        if e.status in ("present", "rest_day_worked"):
            sites_data[key]["total_present"] += 1
        elif e.status in ("absence_unexcused", "absence_excused"):
            sites_data[key]["total_absent"] += 1
        elif e.status == "late" or e.late_minutes > 0:
            sites_data[key]["total_late"] += 1

    sites_list = list(sites_data.values())
    summary = {
        "total_present": sum(s["total_present"] for s in sites_list),
        "total_absent": sum(s["total_absent"] for s in sites_list),
        "total_late": sum(s["total_late"] for s in sites_list),
    }

    return {
        "date": f"{date_from.isoformat()} to {date_to.isoformat()}",
        "sites": sites_list,
        "summary": summary,
    }


# ─────────────────────────────────────────────
# CSV Exports
# ─────────────────────────────────────────────

from fastapi.responses import StreamingResponse
import csv
import io


@router.get("/daily-summary/export", summary="Export supervisor attendance as CSV")
def export_daily_summary_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: Optional[str] = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export daily attendance entries for a date range as CSV."""
    data = get_daily_summary(date_from=date_from, date_to=date_to, site_id=site_id, current_user=current_user, db=db)

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["Date", "Site", "Supervisor", "Guard Name", "Badge", "Status", "Late Minutes", "Note", "Recorded At"])

    for site_entry in data["sites"]:
        for g in site_entry["guards"]:
            writer.writerow([
                site_entry["date"],
                site_entry["site_name"],
                site_entry["supervisor_name"],
                g["name"],
                g["badge_number"],
                g["status"],
                g["late_minutes"],
                g["note"],
                g["created_at"],
            ])

    output.seek(0)
    filename = f"attendance_summary_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export", summary="Export attendance as CSV")
def export_attendance_csv(
    target_date: date = Query(..., description="Date to export"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Export DailyAttendanceEntries for a date as CSV."""
    entries = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.entry_date == target_date).all()
    emp_ids = {e.employee_id for e in entries}
    s_ids = {e.site_id for e in entries}
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(emp_ids)).all()}
    sites_map = {s.site_id: s for s in db.query(Site).filter(Site.site_id.in_(s_ids)).all()}

    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(["Guard Name", "Badge", "Site", "Status", "Late Minutes", "Overtime Hours", "Note", "Date"])

    for e in entries:
        guard = users.get(e.employee_id)
        site = sites_map.get(e.site_id)
        writer.writerow([
            guard.name if guard else "Unknown",
            guard.badge_number if guard else "",
            site.name if site else "Unknown",
            e.status,
            e.late_minutes,
            e.overtime_hours,
            e.note or "",
            e.entry_date.isoformat(),
        ])

    output.seek(0)
    filename = f"attendance_{target_date.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─────────────────────────────────────────────
# Edit & Delete attendance entries (Admin/Accountant)
# ─────────────────────────────────────────────

class AttendanceEntryUpdate(BaseModel):
    status: Optional[str] = None
    late_minutes: Optional[int] = None
    overtime_hours: Optional[float] = None
    overtime_approved: Optional[bool] = None
    overtime_approved_by: Optional[str] = None
    excused_by: Optional[str] = None
    note: Optional[str] = None
    override_reason: Optional[str] = None


@router.put("/{entry_id}", summary="Edit attendance entry (Admin/Accountant)")
def update_attendance_entry(
    entry_id: str,
    update_data: AttendanceEntryUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR)),
    db: Session = Depends(get_db),
):
    entry = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Attendance entry not found")

    if entry.locked and current_user.role not in (UserRole.ADMIN,):
        raise HTTPException(status_code=403, detail="This entry is locked. Only Admin can override.")

    if update_data.status is not None:
        entry.status = update_data.status
    if update_data.late_minutes is not None:
        entry.late_minutes = update_data.late_minutes
    if update_data.overtime_hours is not None:
        entry.overtime_hours = update_data.overtime_hours
    if update_data.overtime_approved is not None:
        entry.overtime_approved = update_data.overtime_approved
    if update_data.overtime_approved_by is not None:
        entry.overtime_approved_by = update_data.overtime_approved_by
    if update_data.excused_by is not None:
        entry.excused_by = update_data.excused_by
    if update_data.note is not None:
        entry.note = update_data.note
    if update_data.override_reason is not None:
        entry.override_reason = update_data.override_reason
        entry.overridden_by = current_user.user_id
        entry.overridden_at = datetime.now(timezone.utc)

    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return {"message": "Updated successfully", "status": entry.status, "entry_id": entry.id}


@router.delete("/{entry_id}", summary="Delete attendance entry (Admin)")
def delete_attendance_entry(
    entry_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    entry = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Attendance entry not found")

    db.delete(entry)
    db.commit()
    return {"message": "Deleted successfully"}


# ─────────────────────────────────────────────
# Comprehensive Attendance Report — already uses DailyAttendanceEntry
# ─────────────────────────────────────────────

@router.get("/report", summary="Comprehensive Attendance Report")
def get_attendance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: Optional[str] = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    from app.models.payroll_formula_config import PayrollFormulaConfig
    from app.models.supervisor_route import SupervisorRoute
    from sqlalchemy.orm import joinedload

    _cfgs = db.query(PayrollFormulaConfig).all()
    _cfg_map = {c.config_key: float(c.value) for c in _cfgs}
    LATE_THRESHOLD_MINUTES = _cfg_map.get("late_threshold_minutes", 10)
    LATE_DEDUCTION_PER_MINUTE = _cfg_map.get("late_deduction_per_minute", 1.0)
    ABSENT_DEDUCTION = _cfg_map.get("absent_day_deduction", 100.0)

    ROLE_ARABIC_MAP = {
        "supervisor": "المشرف",
        "leader": "ليدر",
        "guard": "فرد",
        "outdoor": "فرد خارجي",
        "operations_manager": "لواء",
        "accountant": "محاسب",
        "lady": "ليدي",
        "personnel_officer": "اداري",
        "hr": "موارد بشريه",
    }
    ATTENDANCE_ROLES = set(ROLE_ARABIC_MAP.keys())

    users_query = db.query(User).filter(User.role.in_(ATTENDANCE_ROLES))
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    # ── Load rosters: do NOT restrict by date range ──────────────────────────
    # The report date range applies to attendance *entries* only.
    # Roster assignments describe a user's shift/site/supervisor regardless of
    # when the report is run. Filtering by date_from/date_to causes N/A for all
    # users whose rosters were imported for dates outside the queried range.
    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_dict.keys()))
        .filter(GuardRoster.status != "canceled")
        .all()
    )

    user_rosters = {u_id: [] for u_id in user_dict.keys()}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

    # ── Load supervisor routes: do NOT restrict by date range ────────────────
    # Same reason: route assignments are not always within the report period.
    sup_routes = (
        db.query(SupervisorRoute)
        .filter(SupervisorRoute.supervisor_id.in_(user_dict.keys()))
        .all()
    )
    user_sup_routes = {u_id: [] for u_id in user_dict.keys()}
    for sr in sup_routes:
        user_sup_routes[sr.supervisor_id].append(sr)

    all_site_ids = set()
    for r_list in user_rosters.values():
        for r in r_list:
            if r.shift and r.shift.site_id:
                all_site_ids.add(r.shift.site_id)
    for sr_list in user_sup_routes.values():
        for sr in sr_list:
            all_site_ids.add(sr.site_id)

    site_supervisor_map: dict[str, str] = {}
    if all_site_ids:
        from sqlalchemy import func as sa_func, and_
        sup_route_sub = (
            db.query(
                SupervisorRoute.site_id,
                sa_func.max(SupervisorRoute.assigned_date).label("max_date"),
            )
            .filter(SupervisorRoute.site_id.in_(all_site_ids))
            .group_by(SupervisorRoute.site_id)
            .subquery()
        )
        latest_sup_routes = (
            db.query(SupervisorRoute)
            .join(sup_route_sub, and_(
                SupervisorRoute.site_id == sup_route_sub.c.site_id,
                SupervisorRoute.assigned_date == sup_route_sub.c.max_date,
            ))
            .all()
        )
        sup_ids_for_routes = [sr.supervisor_id for sr in latest_sup_routes]
        if sup_ids_for_routes:
            sup_users = db.query(User).filter(
                User.user_id.in_(sup_ids_for_routes),
                User.role == "supervisor",
            ).all()
            sup_user_map = {u.user_id: u.name for u in sup_users}
            for sr in latest_sup_routes:
                name = sup_user_map.get(sr.supervisor_id)
                if name:
                    site_supervisor_map[sr.site_id] = name

    entries = (
        db.query(DailyAttendanceEntry)
        .filter(DailyAttendanceEntry.employee_id.in_(user_dict.keys()))
        .filter(DailyAttendanceEntry.entry_date >= date_from)
        .filter(DailyAttendanceEntry.entry_date <= date_to)
        .all()
    )

    user_entries = {u_id: [] for u_id in user_dict.keys()}
    for entry in entries:
        user_entries[entry.employee_id].append(entry)

    employees = []
    serial = 1
    for user_id, user in user_dict.items():
        user_roster_list = user_rosters[user_id]
        user_entry_list = user_entries[user_id]
        user_sr_list = user_sup_routes[user_id]

        if site_id:
            user_sites = {r.shift.site_id for r in user_roster_list if r.shift}
            user_sites.update({sr.site_id for sr in user_sr_list})
            if site_id not in user_sites:
                continue

        latest_roster = None
        if user_roster_list:
            latest_roster = sorted(user_roster_list, key=lambda r: r.assigned_date)[-1]

        shift_label = latest_roster.shift.label if latest_roster and latest_roster.shift else "N/A"
        site_name = latest_roster.shift.site.name if latest_roster and latest_roster.shift and latest_roster.shift.site else "N/A"
        shift_time = ""
        if latest_roster and latest_roster.shift:
            st = latest_roster.shift.start_time
            et = latest_roster.shift.end_time
            if st and et:
                shift_time = f"{st.strftime('%H:%M')} - {et.strftime('%H:%M')}"

        if (shift_label == "N/A" or site_name == "N/A") and user_sr_list:
            latest_sr = sorted(user_sr_list, key=lambda r: r.assigned_date)[-1]
            sr_site = db.query(Site).filter(Site.site_id == latest_sr.site_id).first()
            if sr_site and site_name == "N/A":
                site_name = sr_site.name
            if latest_sr.shift_id:
                sr_shift = db.query(Shift).filter(Shift.shift_id == latest_sr.shift_id).first()
                if sr_shift:
                    if shift_label == "N/A":
                        shift_label = sr_shift.label or "N/A"
                    if not shift_time and sr_shift.start_time and sr_shift.end_time:
                        shift_time = f"{sr_shift.start_time.strftime('%H:%M')} - {sr_shift.end_time.strftime('%H:%M')}"

        supervisor_name = "N/A"
        resolved_site_id = None
        if latest_roster and latest_roster.shift:
            resolved_site_id = latest_roster.shift.site_id
        elif user_sr_list:
            latest_sr = sorted(user_sr_list, key=lambda r: r.assigned_date)[-1]
            resolved_site_id = latest_sr.site_id
        if resolved_site_id and resolved_site_id in site_supervisor_map:
            supervisor_name = site_supervisor_map[resolved_site_id]

        days_present = sum(1 for e in user_entry_list if e.status in ("present", "rest_day_worked"))
        days_absent_excused = sum(1 for e in user_entry_list if e.status == 'absence_excused')
        days_absent_unexcused = sum(1 for e in user_entry_list if e.status == 'absence_unexcused')
        days_annual_leave = sum(1 for e in user_entry_list if e.status == 'annual_leave')
        days_sick_leave = sum(1 for e in user_entry_list if e.status == 'sick_leave')
        days_rest = sum(1 for e in user_entry_list if e.status == 'rest')
        days_rest_worked = sum(1 for e in user_entry_list if e.status == 'rest_day_worked')

        late_count = sum(1 for e in user_entry_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        total_overtime_hours = sum(e.overtime_hours for e in user_entry_list)

        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in user_entry_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_absent_unexcused * ABSENT_DEDUCTION
        total_deduction_money = round(late_deduction + absent_deduction, 2)

        leave_date = ""
        if not user.is_active and user.updated_at:
            leave_date = user.updated_at.strftime("%Y-%m-%d")

        employees.append({
            "serial": serial,
            "badge_number": user.badge_number or user.employee_code or "",
            "classification": user.classification if user.classification else ROLE_ARABIC_MAP.get(user.role, user.role or ""),
            "shift_label": shift_label,
            "shift_time": shift_time,
            "supervisor": supervisor_name,
            "site_name": site_name,
            "hire_date": user.hire_date.strftime("%Y-%m-%d") if user.hire_date else (user.created_at.strftime("%Y-%m-%d") if user.created_at else ""),
            "leave_date": leave_date,
            "name": user.name,
            "absence_excused": days_absent_excused,
            "absence_unexcused": days_absent_unexcused,
            "overtime_hours": total_overtime_hours,
            "rest_day_worked": days_rest_worked,
            "late_count": late_count,
            "deductions": total_deduction_money,
            "rest_days": days_rest,
            "annual_leave": days_annual_leave,
            "sick_leave": days_sick_leave,
            "days_present": days_present,
            "user_id": user.user_id,
        })
        serial += 1

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "employees": employees,
    }


@router.get("/export-report", summary="Export comprehensive attendance report as CSV")
def export_attendance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: Optional[str] = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    report = get_attendance_report(date_from=date_from, date_to=date_to, site_id=site_id, current_user=current_user, db=db)
    employees = report["employees"]

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)

    headers = [
        "الاكواد", "مسلسل", "التصنيف", "توقيت العمل", "المشرف", "مشروع",
        "تاريخ التعيين", "تاريخ ترك العمل", "الاسم",
        "غياب باذن", "غياب بدون", "اضافى", "بدل راحه",
        "تاخير", "خصم", "راحة", "اجازة من السنوي", "اجازة مرضي", "ايام العمل التشغيليه",
    ]
    writer.writerow(headers)

    for emp in employees:
        writer.writerow([
            emp["badge_number"], emp["serial"], emp["classification"],
            emp["shift_label"], emp["supervisor"], emp["site_name"],
            emp["hire_date"], emp["leave_date"], emp["name"],
            emp["absence_excused"], emp["absence_unexcused"], emp["overtime_hours"],
            emp["rest_day_worked"], emp["late_count"], emp["deductions"],
            emp["rest_days"], emp["annual_leave"], emp["sick_leave"], emp["days_present"],
        ])

    output.seek(0)
    filename = f"attendance_report_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─────────────────────────────────────────────
# Recent entries (dashboard widget)
# ─────────────────────────────────────────────

@router.get("/recent-daily-entries", summary="Get recent daily attendance entries")
def get_recent_daily_entries(
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entries = (
        db.query(DailyAttendanceEntry)
        .order_by(DailyAttendanceEntry.created_at.desc())
        .limit(limit)
        .all()
    )

    results = []
    for e in entries:
        user = db.query(User).filter(User.user_id == e.employee_id).first()
        site = db.query(Site).filter(Site.site_id == e.site_id).first()
        results.append({
            "name": user.name if user else "Unknown",
            "site": site.name if site else "Unknown",
            "status": e.status,
            "recorded_at": e.created_at.isoformat() if e.created_at else None,
        })

    return {"entries": results}


# ─────────────────────────────────────────────
# Site attendance (read from DailyAttendanceEntry)
# ─────────────────────────────────────────────

@router.get("/site/{site_id}", summary="Attendance for site")
def get_attendance_for_site(
    site_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.LEADER, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Get DailyAttendanceEntries for a site within a date range."""
    q = db.query(DailyAttendanceEntry).filter(DailyAttendanceEntry.site_id == site_id)
    if date_from:
        q = q.filter(DailyAttendanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(DailyAttendanceEntry.entry_date <= date_to)
    entries = q.order_by(DailyAttendanceEntry.entry_date.desc()).all()

    site = db.query(Site).filter(Site.site_id == site_id).first()
    emp_ids = {e.employee_id for e in entries}
    users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_(emp_ids)).all()}

    items = [_entry_to_dict(e, guard=users.get(e.employee_id), site=site) for e in entries]
    return {"records": items, "total": len(items)}
