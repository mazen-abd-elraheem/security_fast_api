"""
SecureTrack Platform — Fake Attendance Detection Routes
Cross-references supervisor-recorded attendance vs GPS workforce data to detect discrepancies.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.attendance_log import AttendanceLog
from app.models.supervisor_visit import SupervisorVisit
from app.models.gps_tracking_ping import GpsTrackingPing
from app.enums import UserRole

router = APIRouter()


@router.get("/detect", summary="Detect fake attendance entries")
def detect_fake_attendance(
    date_from: date = Query(..., description="Start date"),
    date_to: date = Query(..., description="End date"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Cross-reference supervisor-recorded attendance vs GPS workforce data.
    Flags cases where supervisor marked a guard as present/late but GPS shows no activity.
    """
    flagged = []

    # Get all supervisor-recorded attendance logs in the date range
    # where supervisor marked guard as present or late
    attendance_logs = (
        db.query(AttendanceLog)
        .options(
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.guard),
            joinedload(AttendanceLog.roster).joinedload(GuardRoster.shift).joinedload(Shift.site),
            joinedload(AttendanceLog.supervisor),
        )
        .join(GuardRoster, AttendanceLog.roster_id == GuardRoster.roster_id)
        .filter(GuardRoster.assigned_date >= date_from)
        .filter(GuardRoster.assigned_date <= date_to)
        .filter(GuardRoster.status != "canceled")
        .filter(AttendanceLog.status.in_(["present", "late", "replacement"]))
        .filter(AttendanceLog.visit_id.isnot(None))  # Supervisor-recorded only
        .all()
    )

    for log in attendance_logs:
        roster = log.roster
        if not roster or not roster.guard:
            continue

        guard = roster.guard
        shift = roster.shift
        site = shift.site if shift else None
        supervisor = log.supervisor
        assigned_date = roster.assigned_date

        # Check GPS pings for this guard on this date
        start_dt = datetime.combine(assigned_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(assigned_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        gps_ping_count = (
            db.query(GpsTrackingPing)
            .filter(GpsTrackingPing.user_id == guard.user_id)
            .filter(GpsTrackingPing.recorded_at >= start_dt)
            .filter(GpsTrackingPing.recorded_at < end_dt)
            .count()
        )

        # Check if GPS-based attendance logs exist (non-supervisor, auto-checkin)
        auto_checkin_count = (
            db.query(AttendanceLog)
            .filter(AttendanceLog.roster_id == roster.roster_id)
            .filter(AttendanceLog.visit_id.is_(None))  # Auto-checkin (no visit)
            .count()
        )

        # Determine workforce status based on GPS data
        if gps_ping_count == 0 and auto_checkin_count == 0:
            workforce_status = "absent"
            confidence = "high"
        elif gps_ping_count < 5 and auto_checkin_count == 0:
            workforce_status = "minimal_activity"
            confidence = "medium"
        else:
            # GPS confirms presence — no discrepancy
            continue

        flagged.append({
            "date": assigned_date.isoformat(),
            "guard_id": guard.user_id,
            "guard_name": guard.name,
            "guard_badge": guard.badge_number if hasattr(guard, 'badge_number') else "",
            "site_name": site.name if site else "Unknown",
            "site_id": site.site_id if site else None,
            "supervisor_id": supervisor.user_id if supervisor else None,
            "supervisor_name": supervisor.name if supervisor else "Unknown",
            "supervisor_status": log.status,
            "workforce_status": workforce_status,
            "gps_ping_count": gps_ping_count,
            "auto_checkin_count": auto_checkin_count,
            "confidence": confidence,
            "recorded_at": log.recorded_at.isoformat() if log.recorded_at else None,
        })

    # Summary
    high_confidence = sum(1 for f in flagged if f["confidence"] == "high")
    medium_confidence = sum(1 for f in flagged if f["confidence"] == "medium")

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "summary": {
            "total_flagged": len(flagged),
            "high_confidence": high_confidence,
            "medium_confidence": medium_confidence,
        },
        "flagged_entries": flagged,
    }
