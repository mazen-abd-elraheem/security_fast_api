"""
SecureTrack Platform — Attendance Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.attendance import (
    AttendanceRecord, BulkAttendanceRequest,
    AttendanceLogResponse, AttendanceListResponse, AttendanceReportResponse,
)
from app.services.attendance_service import AttendanceService
from app.core.exceptions import SecureTrackException

router = APIRouter()


def _build_log_response(r) -> AttendanceLogResponse:
    guard = r.roster.guard if r.roster else None
    shift = r.roster.shift if r.roster else None
    site = shift.site if shift else None
    return AttendanceLogResponse(
        log_id=r.log_id,
        roster_id=r.roster_id,
        visit_id=r.visit_id,
        supervisor_id=r.supervisor_id,
        status=r.status,
        replacement_guard_id=r.replacement_guard_id,
        notes=r.notes,
        recorded_at=r.recorded_at,
        guard_id=guard.user_id if guard else None,
        guard_name=guard.name if guard else None,
        site_name=site.name if site else None,
        absence_type=getattr(r, 'absence_type', None),
        excused_by=getattr(r, 'excused_by', None),
        overtime_hours=getattr(r, 'overtime_hours', None),
        overtime_approved_by=getattr(r, 'overtime_approved_by', None),
        overtime_approved=getattr(r, 'overtime_approved', False),
        is_rest_day=getattr(r, 'is_rest_day', False),
        is_sick_leave=getattr(r, 'is_sick_leave', False),
        is_annual_leave=getattr(r, 'is_annual_leave', False),
    )

@router.post("", response_model=AttendanceLogResponse, status_code=201, summary="Record attendance")
def record_attendance(
    visit_id: str = Query(..., description="Visit ID this attendance is for"),
    record: AttendanceRecord = ...,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Record attendance for a single guard during a visit."""
    try:
        att = AttendanceService.record_attendance(db, current_user.user_id, visit_id, record)
        # Fetch the full object to populate relationships
        att = db.query(att.__class__).filter_by(log_id=att.log_id).first()
        return _build_log_response(att)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/bulk", status_code=201, summary="Bulk record attendance")
def bulk_record_attendance(
    bulk_data: BulkAttendanceRequest,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """Record attendance for all guards during a visit."""
    try:
        results = AttendanceService.bulk_record(db, current_user.user_id, bulk_data)
        return {"detail": f"{len(results)} attendance records created", "count": len(results)}
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/site/{site_id}", response_model=AttendanceListResponse, summary="Attendance for site")
def get_attendance_for_site(
    site_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.LEADER,
    )),
    db: Session = Depends(get_db),
):
    """Get attendance records for a site within a date range."""
    records = AttendanceService.get_attendance_for_site(db, site_id, date_from, date_to)
    items = [_build_log_response(r) for r in records]
    return AttendanceListResponse(records=items, total=len(items))


@router.get("/guard/{guard_id}", response_model=AttendanceListResponse, summary="Guard attendance")
def get_guard_attendance(
    guard_id: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get attendance history for a guard. Guards can view their own."""
    user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
    if user_role == "guard" and current_user.user_id != guard_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Guards can only view their own attendance")

    records = AttendanceService.get_guard_attendance(db, guard_id, date_from, date_to)
    items = [_build_log_response(r) for r in records]
    return AttendanceListResponse(records=items, total=len(items))

@router.get("/my", response_model=AttendanceListResponse, summary="Get supervisor's recorded attendance today")
def get_my_attendance(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db)
):
    """Get attendance logs recorded by the supervisor/leader today."""
    records = AttendanceService.get_supervisor_attendance_today(db, current_user.user_id, target_date)
    items = [_build_log_response(r) for r in records]
    return AttendanceListResponse(records=items, total=len(items))


@router.get("/supervisor/dashboard", summary="Supervisor attendance dashboard for assigned sites")
def supervisor_attendance_dashboard(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR, UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """
    Returns all guards rostered at the supervisor's assigned sites today,
    along with their attendance status (checked_in or not).
    Powers the supervisor attendance-recording screen.
    """
    from app.models.supervisor_route import SupervisorRoute
    from app.models.guard_roster import GuardRoster
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.attendance_log import AttendanceLog

    if target_date is None:
        target_date = date.today()

    # 1. Get all sites assigned to this supervisor today
    routes = (
        db.query(SupervisorRoute)
        .filter(SupervisorRoute.supervisor_id == current_user.user_id)
        .filter(SupervisorRoute.assigned_date == target_date)
        .all()
    )
    site_ids = [r.site_id for r in routes]
    if not site_ids:
        return {"sites": [], "total_guards": 0, "total_present": 0, "date": target_date.isoformat()}

    # 2. For each site, get guards rostered today
    sites_data = []
    total_guards = 0
    total_present = 0

    for site_id in site_ids:
        site = db.query(Site).filter(Site.site_id == site_id).first()
        if not site:
            continue

        # Get shifts for this site
        shifts = db.query(Shift).filter(Shift.site_id == site_id, Shift.is_active == True).all()
        shift_ids = [s.shift_id for s in shifts]

        if not shift_ids:
            sites_data.append({
                "site_id": site_id,
                "site_name": site.name,
                "guards": [],
                "total": 0,
                "present": 0,
            })
            continue

        # Get guard rosters for today at this site (exclude canceled)
        rosters = (
            db.query(GuardRoster)
            .filter(GuardRoster.shift_id.in_(shift_ids))
            .filter(GuardRoster.assigned_date == target_date)
            .filter(GuardRoster.status != "canceled")
            .all()
        )

        guards = []
        site_present = 0
        seen_guard_ids = set()
        for roster in rosters:
            guard = roster.guard
            shift = roster.shift

            # Skip duplicate guard entries (same guard on multiple shifts)
            if guard and guard.user_id in seen_guard_ids:
                continue
            if guard:
                seen_guard_ids.add(guard.user_id)

            # Check if guard has an attendance log for this roster recorded by THIS supervisor
            att_log = (
                db.query(AttendanceLog)
                .filter(AttendanceLog.roster_id == roster.roster_id)
                .filter(AttendanceLog.supervisor_id == current_user.user_id)
                .first()
            )

            status = att_log.status if att_log else "not_recorded"
            if att_log and att_log.status in ("present", "late"):
                site_present += 1

            guards.append({
                "guard_id": guard.user_id if guard else None,
                "guard_name": guard.name if guard else "Unknown",
                "roster_id": roster.roster_id,
                "shift_label": shift.label if shift else None,
                "shift_time": f"{shift.start_time.strftime('%H:%M')}-{shift.end_time.strftime('%H:%M')}" if shift else None,
                "status": status,
                "recorded_at": att_log.recorded_at.isoformat() if att_log else None,
                "notes": att_log.notes if att_log else None,
                "log_id": att_log.log_id if att_log else None,
                "absence_type": att_log.absence_type if att_log else None,
                "excused_by": att_log.excused_by if att_log else None,
                "overtime_hours": att_log.overtime_hours if att_log else None,
                "overtime_approved_by": att_log.overtime_approved_by if att_log else None,
                "overtime_approved": att_log.overtime_approved if att_log else False,
                "is_rest_day": att_log.is_rest_day if att_log else False,
                "is_sick_leave": att_log.is_sick_leave if att_log else False,
                "is_annual_leave": att_log.is_annual_leave if att_log else False,
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



@router.get("/report", response_model=AttendanceReportResponse, summary="Attendance report")
def get_attendance_report(
    site_id: Optional[str] = Query(None),
    date_from: date = Query(...),
    date_to: date = Query(...),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Generate attendance summary report for a date range."""
    summary = AttendanceService.get_attendance_summary(db, site_id, date_from, date_to)
    return AttendanceReportResponse(
        site_id=site_id,
        date_from=date_from,
        date_to=date_to,
        **summary,
    )


# -- Guard Auto Check-in --

from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.attendance_log import AttendanceLog
from app.services.geo_service import GeoService
import uuid


@router.post("/checkin", status_code=200, summary="Guard auto check-in via GPS")
def guard_checkin(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_role(UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Auto check-in: find the guard's assigned roster for today,
    verify they are within the site's geofence, and create an attendance log.
    """
    from datetime import datetime, timezone
    import logging
    logger = logging.getLogger("securetrack.checkin")

    today = date.today()
    logger.info(f"[CHECKIN] User={current_user.user_id} ({current_user.name}), "
                f"role={current_user.role}, lat={latitude}, lng={longitude}, today={today}")

    # Find today's roster assignment for this guard (exclude canceled)
    roster = (
        db.query(GuardRoster)
        .filter(GuardRoster.guard_id == current_user.user_id)
        .filter(GuardRoster.assigned_date == today)
        .filter(GuardRoster.status != "canceled")
        .first()
    )
    if not roster:
        # Debug: check what rosters exist for this guard
        all_rosters = db.query(GuardRoster).filter(
            GuardRoster.guard_id == current_user.user_id
        ).all()
        logger.warning(f"[CHECKIN] No roster for today={today}. "
                       f"Guard has {len(all_rosters)} total rosters: "
                       f"{[(r.roster_id, str(r.assigned_date)) for r in all_rosters]}")
        return {"status": "no_assignment", "detail": f"No shift assigned for today ({today})"}

    logger.info(f"[CHECKIN] Found roster={roster.roster_id}, shift={roster.shift_id}, "
                f"assigned_date={roster.assigned_date}")

    # Check if already checked in today
    existing = (
        db.query(AttendanceLog)
        .filter(AttendanceLog.roster_id == roster.roster_id)
        .first()
    )
    if existing:
        return {"status": "already_checked_in", "detail": "Already checked in for this shift"}

    # Get the site via shift ? site
    shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
    if not shift:
        return {"status": "error", "detail": "Shift not found"}

    site = db.query(Site).filter(Site.site_id == shift.site_id).first()
    if not site:
        return {"status": "error", "detail": "Site not found"}

    # Calculate distance
    distance = GeoService.haversine_distance_meters(latitude, longitude, site.latitude, site.longitude)
    logger.info(f"[CHECKIN] Site={site.name}, site_lat={site.latitude}, site_lng={site.longitude}, "
                f"radius={site.radius_meters}m, distance={int(distance)}m")

    if distance > site.radius_meters:
        return {
            "status": "out_of_range",
            "detail": f"You are {int(distance)}m away. Must be within {site.radius_meters}m.",
            "distance_meters": int(distance),
        }

    # Create attendance log (no visit/supervisor required for auto check-in)
    log_id = str(uuid.uuid4())
    recorded_at = datetime.now(timezone.utc)
    log = AttendanceLog(
        log_id=log_id,
        roster_id=roster.roster_id,
        visit_id=None,
        supervisor_id=current_user.user_id,
        status="present",
        notes=f"Auto check-in at {int(distance)}m from site center",
        recorded_at=recorded_at,
    )
    db.add(log)
    db.commit()

    logger.info(f"[CHECKIN] SUCCESS — log_id={log_id}, distance={int(distance)}m")

    return {
        "status": "checked_in",
        "detail": f"Checked in to {site.name}",
        "site_name": site.name,
        "distance_meters": int(distance),
        "recorded_at": recorded_at.isoformat(),
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
        existing_log = db.query(AttendanceLog).filter(AttendanceLog.roster_id == r.roster_id).first()

        result["roster_id"] = r.roster_id
        result["shift_id"] = r.shift_id
        result["shift_label"] = shift.label if shift else None
        result["site_name"] = site.name if site else None
        result["site_lat"] = site.latitude if site else None
        result["site_lng"] = site.longitude if site else None
        result["site_radius"] = site.radius_meters if site else None
        result["already_checked_in"] = existing_log is not None
        if existing_log:
            result["existing_log_id"] = existing_log.log_id
            result["existing_log_status"] = existing_log.status

        # Calculate distance from guard's stored location
        if site and current_user.latitude and current_user.longitude:
            dist = GeoService.haversine_distance_meters(current_user.latitude, current_user.longitude, site.latitude, site.longitude)
            result["stored_location_distance_m"] = int(dist)
            result["within_geofence"] = dist <= site.radius_meters

    return result


# -- CSV Export --

from fastapi.responses import StreamingResponse
import csv
import io

@router.get("/export", summary="Export attendance as CSV")
def export_attendance_csv(
    target_date: date = Query(..., description="Date to export"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Export attendance records for a date as CSV."""
    from sqlalchemy.orm import joinedload

    logs = (
        db.query(AttendanceLog)
        .options(
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.guard),
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.shift).joinedload(Shift.site),
        )
        .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
        .filter(GuardRoster.assigned_date == target_date)
        .all()
    )

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["Guard Name", "Badge", "Site", "Shift", "Status", "Check-in Time", "Notes"])

    for log in logs:
        guard = log.roster.guard if log.roster else None
        shift = log.roster.shift if log.roster else None
        site = shift.site if shift else None
        writer.writerow([
            guard.name if guard else "Unknown",
            guard.badge_number if guard else "",
            site.name if site else "Unknown",
            shift.label if shift else "",
            log.status,
            log.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if log.recorded_at else "",
            log.notes or "",
        ])

    output.seek(0)
    filename = f"attendance_{target_date.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -- Supervisor Attendance Daily Summary (for Admin) --

@router.get("/daily-summary", summary="Supervisor attendance daily summary")
def get_daily_summary(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: str = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """
    Get all supervisor-recorded attendance for a date, grouped by site.
    This is the manual roll-call data taken by supervisors during visits.
    """
    from sqlalchemy.orm import joinedload

    query = (
        db.query(AttendanceLog)
        .options(
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.guard),
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(AttendanceLog.supervisor),
            joinedload(AttendanceLog.visit),
        )
        .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
        .outerjoin(Shift, GuardRoster.shift_id == Shift.shift_id)
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
    )
    if site_id:
        query = query.filter(Shift.site_id == site_id)
    logs = query.all()

    # Group by site and date
    sites_map = {}
    for log in logs:
        guard = log.roster.guard if log.roster else None
        shift = log.roster.shift if log.roster else None
        site = shift.site if shift else None
        site_name = site.name if site else "Unknown"
        supervisor = log.supervisor
        date_str = log.roster.assigned_date.isoformat() if log.roster else "Unknown"
        key = f"{date_str}_{site_name}"

        if key not in sites_map:
            sites_map[key] = {
                "date": date_str,
                "site_name": site_name,
                "supervisor_name": supervisor.name if supervisor else "Unknown",
                "visit_time": log.recorded_at.strftime("%H:%M") if log.recorded_at else "",
                "guards": [],
                "total_present": 0,
                "total_absent": 0,
                "total_late": 0,
            }

        guard_entry = {
            "log_id": log.log_id,
            "roster_id": log.roster_id,
            "name": guard.name if guard else "Unknown",
            "badge_number": guard.badge_number if guard else "",
            "employee_code": guard.employee_code if guard else "",
            "classification": guard.classification if guard else "",
            "status": log.status,
            "notes": log.notes or "",
            "recorded_at": log.recorded_at.isoformat() if log.recorded_at else "",
        }
        sites_map[key]["guards"].append(guard_entry)

        if log.status == "present":
            sites_map[key]["total_present"] += 1
        elif log.status == "absent":
            sites_map[key]["total_absent"] += 1
        elif log.status == "late":
            sites_map[key]["total_late"] += 1

    sites_list = list(sites_map.values())

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


@router.get("/daily-summary/export", summary="Export supervisor attendance as CSV")
def export_daily_summary_csv(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: str = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO)),
    db: Session = Depends(get_db),
):
    """Export supervisor-recorded attendance for a date as CSV."""
    from sqlalchemy.orm import joinedload

    query = (
        db.query(AttendanceLog)
        .options(
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.guard),
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(AttendanceLog.supervisor),
        )
        .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
        .outerjoin(Shift, GuardRoster.shift_id == Shift.shift_id)
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
    )
    if site_id:
        query = query.filter(Shift.site_id == site_id)
    logs = query.all()

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Excel
    writer = csv.writer(output)
    writer.writerow(["Date", "Site", "Supervisor", "Guard Name", "Badge", "Status", "Notes", "Recorded At"])

    for log in logs:
        guard = log.roster.guard if log.roster else None
        shift = log.roster.shift if log.roster else None
        site = shift.site if shift else None
        supervisor = log.supervisor

        writer.writerow([
            log.roster.assigned_date.isoformat() if log.roster else "Unknown",
            site.name if site else "Unknown",
            supervisor.name if supervisor else "Unknown",
            guard.name if guard else "Unknown",
            guard.badge_number if guard else "",
            log.status,
            log.notes or "",
            log.recorded_at.strftime("%Y-%m-%d %H:%M:%S") if log.recorded_at else "",
        ])

    output.seek(0)
    filename = f"attendance_summary_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -- Accountant Edit & Delete --
class AttendanceUpdate(BaseModel):
    status: str
    notes: Optional[str] = None

@router.put("/{log_id}", summary="Edit attendance record (Accountant)")
def update_attendance(
    log_id: str,
    update_data: AttendanceUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    log = db.query(AttendanceLog).filter(AttendanceLog.log_id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    
    log.status = update_data.status
    if update_data.notes is not None:
        log.notes = update_data.notes
        
    db.commit()
    db.refresh(log)
    return {"message": "Updated successfully", "status": log.status}

@router.delete("/{log_id}", summary="Delete attendance record (Accountant)")
def delete_attendance(
    log_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    log = db.query(AttendanceLog).filter(AttendanceLog.log_id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    
    db.delete(log)
    db.commit()
    return {"message": "Deleted successfully"}
# -- Comprehensive Attendance Report --

@router.get("/report", summary="Comprehensive Attendance Report")
def get_attendance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: str = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    from sqlalchemy.orm import joinedload
    from app.models.daily_attendance_entry import DailyAttendanceEntry
    from app.api.v1.payroll import LATE_THRESHOLD_MINUTES, LATE_DEDUCTION_PER_MINUTE, ABSENT_DEDUCTION

    users_query = db.query(User).filter(User.is_active == True)
    users = users_query.all()
    user_dict = {u.user_id: u for u in users}

    rosters = (
        db.query(GuardRoster)
        .options(joinedload(GuardRoster.shift).joinedload(Shift.site))
        .filter(GuardRoster.guard_id.in_(user_dict.keys()))
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .all()
    )

    user_rosters = {u_id: [] for u_id in user_dict.keys()}
    for roster in rosters:
        user_rosters[roster.guard_id].append(roster)

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
        
        # If site filter is provided, skip users not assigned to this site in this period
        if site_id:
            user_sites = {r.shift.site_id for r in user_roster_list if r.shift}
            if site_id not in user_sites:
                continue

        # Get latest roster for shift/site/supervisor info
        latest_roster = None
        if user_roster_list:
            latest_roster = sorted(user_roster_list, key=lambda r: r.assigned_date)[-1]
            
        shift_label = latest_roster.shift.label if latest_roster and latest_roster.shift else "N/A"
        site_name = latest_roster.shift.site.name if latest_roster and latest_roster.shift and latest_roster.shift.site else "N/A"
        supervisor_name = "N/A" # Supervisors aren't directly on GuardRoster, but site managers or roll callers are.

        # Aggregate counts
        days_present = sum(1 for e in user_entry_list if e.status == 'present')
        days_absent_excused = sum(1 for e in user_entry_list if e.status == 'absence_excused')
        days_absent_unexcused = sum(1 for e in user_entry_list if e.status == 'absence_unexcused')
        days_annual_leave = sum(1 for e in user_entry_list if e.status == 'annual_leave')
        days_sick_leave = sum(1 for e in user_entry_list if e.status == 'sick_leave')
        days_rest = sum(1 for e in user_entry_list if e.status == 'rest')
        days_rest_worked = sum(1 for e in user_entry_list if e.status == 'rest_day_worked')
        
        late_count = sum(1 for e in user_entry_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        total_overtime_hours = sum(e.overtime_hours for e in user_entry_list)
        
        # Calculate deduction money
        late_deduction = sum((e.late_minutes * LATE_DEDUCTION_PER_MINUTE) for e in user_entry_list if e.late_minutes > LATE_THRESHOLD_MINUTES)
        absent_deduction = days_absent_unexcused * ABSENT_DEDUCTION
        total_deduction_money = round(late_deduction + absent_deduction, 2)

        # Leave date logic
        leave_date = ""
        if not user.is_active and user.updated_at:
            leave_date = user.updated_at.strftime("%Y-%m-%d")

        employees.append({
            "serial": serial,
            "badge_number": user.employee_code or user.badge_number or "",
            "classification": user.classification or "",
            "shift_label": shift_label,
            "supervisor": supervisor_name,
            "site_name": site_name,
            "hire_date": user.hire_date.strftime("%Y-%m-%d") if user.hire_date else "",
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
        "employees": employees
    }


@router.get("/export-report", summary="Export comprehensive attendance report as CSV")
def export_attendance_report(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    site_id: str = Query(None, description="Filter by site ID"),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.CEO, UserRole.HR)),
    db: Session = Depends(get_db),
):
    import io
    import csv
    from fastapi.responses import StreamingResponse
    
    report = get_attendance_report(date_from=date_from, date_to=date_to, site_id=site_id, current_user=current_user, db=db)
    employees = report["employees"]

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    
    headers = [
        "الاكواد",
        "مسلسل",
        "التصنيف",
        "توقيت العمل",
        "المشرف",
        "مشروع",
        "تاريخ التعيين",
        "تاريخ ترك العمل",
        "الاســـــــــــــــــــــــــــــم",
        "غياب باذن",
        "غياب بدون",
        "اضافى",
        "بدل راحه",
        "تاخير",
        "خصم",
        "راحة",
        "اجازة من السنوي",
        "اجازة مرضي",
        "ايام العمل التشغيليه"
    ]
    writer.writerow(headers)
    
    for emp in employees:
        writer.writerow([
            emp["badge_number"],
            emp["serial"],
            emp["classification"],
            emp["shift_label"],
            emp["supervisor"],
            emp["site_name"],
            emp["hire_date"],
            emp["leave_date"],
            emp["name"],
            emp["absence_excused"],
            emp["absence_unexcused"],
            emp["overtime_hours"],
            emp["rest_day_worked"],
            emp["late_count"],
            emp["deductions"],
            emp["rest_days"],
            emp["annual_leave"],
            emp["sick_leave"],
            emp["days_present"]
        ])
        
    output.seek(0)
    filename = f"attendance_report_{date_from.isoformat()}_to_{date_to.isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
