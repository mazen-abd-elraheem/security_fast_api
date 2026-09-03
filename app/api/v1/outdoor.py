"""
SecureTrack Platform — Outdoor Role Routes
Self check-in/check-out and geofence breach alerting for outdoor personnel.
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.attendance_log import AttendanceLog
from app.models.notification import Notification
from app.enums import UserRole
from app.services.geo_service import GeoService

router = APIRouter()





@router.post("/checkin", status_code=200, summary="Outdoor self check-in via GPS")
def outdoor_checkin(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_role(UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Self check-in for outdoor personnel.
    Finds the user's roster assignment for today, verifies GPS is within geofence,
    and creates an attendance log entry.
    """
    try:
        today = date.today()

        # Find today's roster assignment (exclude canceled)
        roster = (
            db.query(GuardRoster)
            .filter(GuardRoster.guard_id == current_user.user_id)
            .filter(GuardRoster.assigned_date == today)
            .filter(GuardRoster.status != "canceled")
            .first()
        )
        if not roster:
            return {"status": "no_assignment", "detail": "No shift assigned for today"}

        # Check if already checked in
        existing = (
            db.query(AttendanceLog)
            .filter(AttendanceLog.roster_id == roster.roster_id)
            .first()
        )
        if existing and existing.checkout_at is None:
            return {"status": "already_checked_in", "detail": "Already checked in for this shift"}
        if existing and existing.checkout_at is not None:
            return {"status": "already_completed", "detail": "Shift already completed (checked out)"}

        # Get the site via shift → site
        shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
        if not shift:
            return {"status": "error", "detail": "Shift not found"}

        site = db.query(Site).filter(Site.site_id == shift.site_id).first()
        if not site:
            return {"status": "error", "detail": "Site not found"}

        # Calculate distance
        distance = GeoService.haversine_distance_meters(latitude, longitude, site.latitude, site.longitude)
        if distance > site.radius_meters:
            return {
                "status": "out_of_range",
                "detail": f"You are {int(distance)}m away. Must be within {site.radius_meters}m.",
                "distance_meters": int(distance),
            }

        # Create attendance log
        log_id = str(uuid.uuid4())
        db.execute(
            AttendanceLog.__table__.insert().values(
                log_id=log_id,
                roster_id=roster.roster_id,
                visit_id=None,
                supervisor_id=current_user.user_id,  # self-reported
                status="present",
                notes=f"Outdoor self check-in at {int(distance)}m from site center",
                recorded_at=datetime.now(timezone.utc),
                checkout_at=None,
            )
        )
        db.commit()

        return {
            "status": "checked_in",
            "detail": f"Checked in to {site.name}",
            "log_id": log_id,
            "site_name": site.name,
            "site_id": site.site_id,
            "site_lat": site.latitude,
            "site_lng": site.longitude,
            "site_radius": site.radius_meters,
            "distance_meters": int(distance),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        import traceback
        from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot
        return {"status": "error", "detail": str(e), "trace": traceback.format_exc()}


@router.post("/checkout", status_code=200, summary="Outdoor self check-out via GPS")
def outdoor_checkout(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_role(UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Self check-out for outdoor personnel.
    Updates the existing attendance log with checkout timestamp.
    """
    today = date.today()

    # Find today's roster assignment (exclude canceled)
    roster = (
        db.query(GuardRoster)
        .filter(GuardRoster.guard_id == current_user.user_id)
        .filter(GuardRoster.assigned_date == today)
        .filter(GuardRoster.status != "canceled")
        .first()
    )
    if not roster:
        return {"status": "error", "detail": "No shift assigned for today"}

    # Find the attendance log
    log = (
        db.query(AttendanceLog)
        .filter(AttendanceLog.roster_id == roster.roster_id)
        .filter(AttendanceLog.checkout_at == None)  # noqa: E711
        .first()
    )
    if not log:
        return {"status": "not_checked_in", "detail": "You are not checked in"}

    # Update checkout
    now = datetime.now(timezone.utc)
    log.checkout_at = now
    log.notes = (log.notes or "") + f" | Checked out at {int(GeoService.haversine_distance_meters(latitude, longitude, 0, 0))}m"
    db.commit()

    return {
        "status": "checked_out",
        "detail": "Successfully checked out",
        "checkout_at": now.isoformat(),
    }


@router.post("/geofence-breach", status_code=200, summary="Report geofence breach to admins")
def report_geofence_breach(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    site_id: str = Query(..., description="The site the user left"),
    current_user: User = Depends(require_role(UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Called by the app when an outdoor user leaves the site geofence mid-shift.
    Creates a notification for all admin users.
    """
    site = db.query(Site).filter(Site.site_id == site_id).first()
    site_name = site.name if site else "Unknown Site"

    distance = 0
    if site:
        distance = int(GeoService.haversine_distance_meters(latitude, longitude, site.latitude, site.longitude))

    # Find all admin users
    admins = (
        db.query(User)
        .filter(User.role.in_([UserRole.ADMIN.value]))
        .filter(User.is_active == True)  # noqa: E712
        .all()
    )

    user_name = current_user.name if hasattr(current_user, 'name') else "Unknown"

    for admin in admins:
        notif = Notification(
            notification_id=str(uuid.uuid4()),
            user_id=admin.user_id,
            notif_type="geofence_breach",
            title=f"⚠ Geofence Breach: {user_name}",
            message=f"{user_name} left {site_name} during their shift. Distance: {distance}m from site.",
            reference_id=current_user.user_id,
            reference_type="user",
        )
        db.add(notif)

    db.commit()

    return {
        "status": "reported",
        "detail": f"Geofence breach reported. {len(admins)} admin(s) notified.",
        "distance_meters": distance,
    }
