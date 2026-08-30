"""
SecureTrack Platform â€” User Routes
Profile management, location updates, and user listing.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.user import (
    UserResponse, UserUpdate, UserLocationUpdate, UserListResponse,
    AdminUserCreate, AdminUserUpdate,
)
from app.services.user_service import UserService
from app.core.exceptions import SecureTrackException

router = APIRouter()


# ==========================================
# Self-service endpoints
# ==========================================

@router.get("/me", response_model=UserResponse, summary="Get my profile")
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Get the authenticated user's profile."""
    return current_user


@router.put("/me", response_model=UserResponse, summary="Update my profile")
def update_my_profile(
    update_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the authenticated user's profile."""
    try:
        return UserService.update_profile(db, current_user.user_id, update_data)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/me/location", response_model=UserResponse, summary="Update my GPS location")
def update_my_location(
    location: UserLocationUpdate,
    current_user: User = Depends(require_role(
        UserRole.SUPERVISOR, UserRole.GUARD, UserRole.OUTDOOR,
    )),
    db: Session = Depends(get_db),
):
    """Update GPS location and auto-check-in for guards/outdoor if within geofence."""
    try:
        result = UserService.update_location(db, current_user.user_id, location)

        # Auto-check-in for guard/outdoor roles
        user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
        if user_role in ('guard', 'outdoor'):
            _try_auto_checkin(db, current_user, location.latitude, location.longitude)

        return result
    except SecureTrackException as e:
        handle_service_exception(e)


def _try_auto_checkin(db: Session, user: User, lat: float, lng: float):
    """Silently attempt auto-checkin when guard/outdoor location is updated."""
    import uuid
    import logging
    from datetime import date, datetime, timezone
    from math import radians, cos, sin, asin, sqrt
    from app.models.guard_roster import GuardRoster
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.attendance_log import AttendanceLog
    from app.models.gps_tracking_ping import GpsTrackingPing

    logger = logging.getLogger("securetrack.auto_checkin")

    try:
        today = date.today()

        # Find today's roster (exclude canceled assignments)
        roster = (
            db.query(GuardRoster)
            .filter(GuardRoster.guard_id == user.user_id)
            .filter(GuardRoster.assigned_date == today)
            .filter(GuardRoster.status != "canceled")
            .first()
        )
        if not roster:
            return

        # Get site
        shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
        if not shift:
            return
        site = db.query(Site).filter(Site.site_id == shift.site_id).first()
        if not site:
            return

        # Haversine distance
        lat1, lon1, lat2, lon2 = map(radians, [lat, lng, site.latitude, site.longitude])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        distance = 2 * asin(sqrt(a)) * 6371000

        now_utc = datetime.now(timezone.utc)

        # Find existing attendance log for this roster
        existing = db.query(AttendanceLog).filter(
            AttendanceLog.roster_id == roster.roster_id
        ).order_by(AttendanceLog.recorded_at.desc()).first()

        if distance > site.radius_meters:
            # OUTSIDE geofence â€” only checkout after 5+ consecutive outside pings
            if existing and not existing.checkout_at:
                recent_pings = (
                    db.query(GpsTrackingPing)
                    .filter(
                        GpsTrackingPing.user_id == user.user_id,
                        GpsTrackingPing.roster_id == roster.roster_id,
                    )
                    .order_by(GpsTrackingPing.recorded_at.desc())
                    .limit(5)
                    .all()
                )
                if len(recent_pings) >= 5 and all(not p.is_within_geofence for p in recent_pings):
                    existing.checkout_at = now_utc
                    existing.notes = (existing.notes or "") + " | Auto check-out (left geofence 5+ min)"
                    db.commit()
                    logger.info(f"[AUTO-CHECKOUT] {user.name} left {site.name} ({int(distance)}m) after 5+ outside pings")
            return

        # INSIDE geofence
        if existing and not existing.checkout_at:
            # Already checked in, still inside. Session stays open.
            pass
        elif existing and existing.checkout_at:
            # RE-ENTRY: guard returned to geofence after checkout.
            # Reopen the SAME session â€” accumulate outside time.
            outside_gap = (now_utc - existing.checkout_at).total_seconds()
            if outside_gap > 0:
                existing.total_outside_seconds = (existing.total_outside_seconds or 0) + outside_gap
            existing.checkout_at = None  # reopen the session
            existing.notes = (existing.notes or "") + f" | Re-entered geofence (was outside {int(outside_gap)}s)"
            db.commit()
            logger.info(f"[RE-ENTRY] {user.name} re-entered {site.name} ({int(distance)}m), was outside {int(outside_gap)}s")
        else:
            # First time checking in today
            log = AttendanceLog(
                log_id=str(uuid.uuid4()),
                roster_id=roster.roster_id,
                visit_id=None,
                supervisor_id=user.user_id,
                status="present",
                notes=f"Auto check-in at {int(distance)}m from site center",
                recorded_at=now_utc,
                total_outside_seconds=0.0,
            )
            db.add(log)
            db.commit()
            logger.info(f"[AUTO-CHECKIN] {user.name} checked in to {site.name} ({int(distance)}m)")
            
    except Exception as e:
        logger.error(f"[AUTO-CHECKIN] Failed for {user.user_id}: {e}")
        db.rollback()


# ==========================================
# Admin endpoints
# ==========================================

@router.get("", response_model=UserListResponse, summary="List users")
def list_users(
    role: Optional[str] = Query(None, description="Filter by role"),
    region: Optional[str] = Query(None, description="Filter by region"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """List users with optional filtering. Admin only."""
    return UserService.list_users(db, role=role, region=region, is_active=is_active, skip=skip, limit=limit)


@router.post("", response_model=UserResponse, status_code=201, summary="Admin creates a user")
def admin_create_user(
    user_data: AdminUserCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin creates any type of user account."""
    try:
        return UserService.admin_create_user(db, user_data)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
def get_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get a user's profile by ID. Admin only."""
    try:
        user = UserService.get_by_id(db, user_id)
        if not user:
            from app.core.exceptions import NotFoundException
            raise NotFoundException("User", user_id)
        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/{user_id}", response_model=UserResponse, summary="Admin updates a user")
def admin_update_user(
    user_id: str,
    update_data: AdminUserUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT)),
    db: Session = Depends(get_db),
):
    """Admin-level user update â€” can change any field."""
    try:
        return UserService.admin_update_user(db, user_id, update_data)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.delete("/{user_id}", response_model=UserResponse, summary="Deactivate a user")
def deactivate_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Soft-delete: deactivate a user account."""
    try:
        return UserService.deactivate_user(db, user_id)
    except SecureTrackException as e:
        handle_service_exception(e)


