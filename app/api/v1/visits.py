"""
SecureTrack Platform — Visit Routes (Core Geofence Engine)
GPS-verified check-in/check-out endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.visit import CheckInRequest, CheckOutRequest, VisitResponse, VisitListResponse
from app.services.visit_service import VisitService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("/check-in", response_model=VisitResponse, status_code=201, summary="GPS-verified check-in")
def check_in(
    checkin_data: CheckInRequest,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """
    GPS-verified check-in at a site.

    - Validates supervisor is within the site's geofence radius
    - Records exact GPS coordinates and distance from site center
    - Updates route status to in_progress
    - Returns 403 if outside geofence with exact distance info
    """
    try:
        visit = VisitService.check_in(
            db,
            supervisor_id=current_user.user_id,
            site_id=checkin_data.site_id,
            latitude=checkin_data.latitude,
            longitude=checkin_data.longitude,
            photo_url=checkin_data.photo_url,
            notes=checkin_data.notes,
        )
        site = visit.site
        return VisitResponse(
            visit_id=visit.visit_id,
            supervisor_id=visit.supervisor_id,
            supervisor_name=current_user.name,
            site_id=visit.site_id,
            site_name=site.name if site else None,
            route_id=visit.route_id,
            check_in_time=visit.check_in_time,
            check_in_lat=visit.check_in_lat,
            check_in_lng=visit.check_in_lng,
            distance_from_site=visit.distance_from_site,
            is_verified=visit.is_verified,
            photo_url=visit.photo_url,
            notes=visit.notes,
            created_at=visit.created_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/{visit_id}/check-out", response_model=VisitResponse, summary="Check-out from site")
def check_out(
    visit_id: str,
    checkout_data: CheckOutRequest,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Check out from a site visit."""
    try:
        visit = VisitService.check_out(
            db,
            visit_id=visit_id,
            supervisor_id=current_user.user_id,
            latitude=checkout_data.latitude,
            longitude=checkout_data.longitude,
            notes=checkout_data.notes,
        )
        return VisitResponse(
            visit_id=visit.visit_id,
            supervisor_id=visit.supervisor_id,
            supervisor_name=current_user.name,
            site_id=visit.site_id,
            site_name=visit.site.name if visit.site else None,
            route_id=visit.route_id,
            check_in_time=visit.check_in_time,
            check_in_lat=visit.check_in_lat,
            check_in_lng=visit.check_in_lng,
            distance_from_site=visit.distance_from_site,
            check_out_time=visit.check_out_time,
            check_out_lat=visit.check_out_lat,
            check_out_lng=visit.check_out_lng,
            is_verified=visit.is_verified,
            photo_url=visit.photo_url,
            notes=visit.notes,
            created_at=visit.created_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/my", response_model=VisitListResponse, summary="My visits today")
def get_my_visits(
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Get the supervisor's visits for today (or a specific date)."""
    if not target_date:
        target_date = date.today()
    visits = VisitService.get_visits_for_supervisor(db, current_user.user_id, target_date)
    items = []
    for v in visits:
        items.append(VisitResponse(
            visit_id=v.visit_id, supervisor_id=v.supervisor_id,
            supervisor_name=current_user.name, site_id=v.site_id,
            site_name=v.site.name if v.site else None, route_id=v.route_id,
            check_in_time=v.check_in_time, check_in_lat=v.check_in_lat,
            check_in_lng=v.check_in_lng, distance_from_site=v.distance_from_site,
            check_out_time=v.check_out_time, check_out_lat=v.check_out_lat,
            check_out_lng=v.check_out_lng, is_verified=v.is_verified,
            photo_url=v.photo_url, notes=v.notes, created_at=v.created_at,
        ))
    return VisitListResponse(visits=items, total=len(items))


@router.get("/site/{site_id}", response_model=VisitListResponse, summary="Visits for a site")
def get_visits_for_site(
    site_id: str,
    target_date: Optional[date] = Query(None),
    current_user: User = Depends(require_role(
        UserRole.ADMIN,
    )),
    db: Session = Depends(get_db),
):
    """Get all visits for a site on a specific date."""
    visits = VisitService.get_visits_for_site(db, site_id, target_date)
    items = []
    for v in visits:
        items.append(VisitResponse(
            visit_id=v.visit_id, supervisor_id=v.supervisor_id,
            supervisor_name=v.supervisor.name if v.supervisor else None,
            site_id=v.site_id, site_name=v.site.name if v.site else None,
            route_id=v.route_id, check_in_time=v.check_in_time,
            check_in_lat=v.check_in_lat, check_in_lng=v.check_in_lng,
            distance_from_site=v.distance_from_site, check_out_time=v.check_out_time,
            check_out_lat=v.check_out_lat, check_out_lng=v.check_out_lng,
            is_verified=v.is_verified, photo_url=v.photo_url, notes=v.notes,
            created_at=v.created_at,
        ))
    return VisitListResponse(visits=items, total=len(items))
