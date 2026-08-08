"""
SecureTrack Platform — GPS Tracking Routes
Receives periodic GPS pings from guard/outdoor users.
Server-side geofence validation and presence hour computation.
"""
import uuid
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.guard_roster import GuardRoster
from app.models.shift import Shift
from app.models.site import Site
from app.models.gps_tracking_ping import GpsTrackingPing
from app.models.attendance_log import AttendanceLog
from app.enums import UserRole
from app.services.geo_service import GeoService

router = APIRouter()

# Maximum gap (in seconds) between pings before considering it a break
PRESENCE_GAP_THRESHOLD_SECONDS = 300  # 5 minutes





def compute_presence_hours_from_pings(pings: list[GpsTrackingPing]) -> float:
    """
    Compute total presence hours from a list of GPS pings.
    Counts continuous intervals where the user was within the geofence.
    A gap > PRESENCE_GAP_THRESHOLD_SECONDS is treated as a break.
    """
    in_fence = [p for p in sorted(pings, key=lambda p: p.recorded_at) if p.is_within_geofence]
    if len(in_fence) < 2:
        return 0.0

    total_seconds = 0.0
    session_start = in_fence[0].recorded_at
    prev_time = in_fence[0].recorded_at

    for ping in in_fence[1:]:
        gap = (ping.recorded_at - prev_time).total_seconds()
        if gap > PRESENCE_GAP_THRESHOLD_SECONDS:
            # Close previous session
            total_seconds += (prev_time - session_start).total_seconds()
            # Add one interval worth of time for the last ping in the session
            total_seconds += min(gap, 60)  # count up to 60s for the last ping
            session_start = ping.recorded_at
        prev_time = ping.recorded_at

    # Close final session
    total_seconds += (prev_time - session_start).total_seconds()
    # Add one interval for the final ping
    total_seconds += 60  # assume the guard was present for 60s after the last ping

    return round(total_seconds / 3600.0, 2)


@router.post("/ping", status_code=200, summary="GPS tracking ping")
def tracking_ping(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    current_user: User = Depends(require_role(UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """
    Receive a GPS ping from a guard/outdoor user.
    Silently stores the ping with geofence validation.
    Called automatically by the app's background service every ~60 seconds.
    """
    today = date.today()

    # Find today's roster
    roster = (
        db.query(GuardRoster)
        .filter(GuardRoster.guard_id == current_user.user_id)
        .filter(GuardRoster.assigned_date == today)
        .first()
    )

    roster_id = None
    is_within = False
    distance = None

    if roster:
        roster_id = roster.roster_id
        # Get site via shift
        shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
        if shift:
            site = db.query(Site).filter(Site.site_id == shift.site_id).first()
            if site:
                distance = GeoService.haversine_distance_meters(latitude, longitude, site.latitude, site.longitude)
                is_within = distance <= site.radius_meters

    # Store the ping
    now_utc = datetime.now(timezone.utc)
    ping = GpsTrackingPing(
        ping_id=str(uuid.uuid4()),
        user_id=current_user.user_id,
        roster_id=roster_id,
        latitude=latitude,
        longitude=longitude,
        is_within_geofence=is_within,
        distance_meters=round(distance, 1) if distance is not None else None,
        recorded_at=now_utc,
    )
    db.add(ping)

    # Also update user's stored location
    current_user.latitude = latitude
    current_user.longitude = longitude

    # ── Auto check-in / check-out (GPS-driven) ──
    auto_action = None
    if roster:
        existing_log = (
            db.query(AttendanceLog)
            .filter(AttendanceLog.roster_id == roster.roster_id)
            .first()
        )

        if is_within and not existing_log:
            # AUTO CHECK-IN: guard entered geofence, no attendance yet → create one
            db.add(AttendanceLog(
                log_id=str(uuid.uuid4()),
                roster_id=roster.roster_id,
                visit_id=None,
                supervisor_id=current_user.user_id,
                status="present",
                notes="Auto check-in via GPS ping",
                recorded_at=now_utc,
                checkout_at=None,
            ))
            auto_action = "checked_in"

        elif not is_within and existing_log and existing_log.checkout_at is None:
            # Check if guard has been OUTSIDE geofence for 5+ consecutive minutes
            # Look at last 5 pings (5 × 60s = 5 min window)
            recent_pings = (
                db.query(GpsTrackingPing)
                .filter(
                    GpsTrackingPing.user_id == current_user.user_id,
                    GpsTrackingPing.roster_id == roster.roster_id,
                )
                .order_by(GpsTrackingPing.recorded_at.desc())
                .limit(5)
                .all()
            )
            # All recent pings outside geofence → auto checkout
            if len(recent_pings) >= 5 and all(not p.is_within_geofence for p in recent_pings):
                existing_log.checkout_at = now_utc
                existing_log.notes = (existing_log.notes or "") + " | Auto check-out (left geofence 5+ min)"
                auto_action = "checked_out"

    db.commit()

    return {
        "status": "ok",
        "is_within_geofence": is_within,
        "distance_meters": int(distance) if distance is not None else None,
        "auto_action": auto_action,
    }


@router.get("/presence/{user_id}", summary="Get presence data for a user on a date")
def get_presence(
    user_id: str,
    target_date: date = Query(..., description="Date to query"),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Admin endpoint: returns all GPS pings for a user on a date,
    with computed total presence hours.
    """
    start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    pings = (
        db.query(GpsTrackingPing)
        .filter(GpsTrackingPing.user_id == user_id)
        .filter(GpsTrackingPing.recorded_at >= start_dt)
        .filter(GpsTrackingPing.recorded_at < end_dt)
        .order_by(GpsTrackingPing.recorded_at)
        .all()
    )

    total_pings = len(pings)
    in_fence_pings = sum(1 for p in pings if p.is_within_geofence)
    presence_hours = compute_presence_hours_from_pings(pings)

    return {
        "user_id": user_id,
        "date": target_date.isoformat(),
        "total_pings": total_pings,
        "in_fence_pings": in_fence_pings,
        "presence_hours": presence_hours,
        "pings": [
            {
                "recorded_at": p.recorded_at.isoformat(),
                "latitude": p.latitude,
                "longitude": p.longitude,
                "is_within_geofence": p.is_within_geofence,
                "distance_meters": p.distance_meters,
            }
            for p in pings
        ],
    }
