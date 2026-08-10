"""
SecureTrack Platform — Supervisor Route Routes
Daily route assignment and itinerary endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.route import RouteCreate, BulkRouteCreate, RouteResponse, DailyItineraryResponse
from app.services.route_service import RouteService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("", status_code=201, summary="Assign daily route")
def assign_route(
    route_data: RouteCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a daily route (list of sites) to a supervisor."""
    try:
        routes = RouteService.assign_route(db, route_data)
        return {"detail": f"{len(routes)} site assignments created", "count": len(routes)}
    except SecureTrackException as e:
        handle_service_exception(e)


@router.post("/bulk", status_code=201, summary="Bulk assign supervisor to date range")
def bulk_assign_route(
    bulk_data: BulkRouteCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Assign a supervisor to the same sites across multiple dates in one call."""
    try:
        routes = RouteService.bulk_assign_route(db, bulk_data.supervisor_id, bulk_data.dates, bulk_data.sites)
        return {"detail": f"{len(routes)} assignments created across {len(bulk_data.dates)} dates", "count": len(routes)}
    except SecureTrackException as e:
        handle_service_exception(e)


def _build_itinerary(routes, supervisor_id, supervisor_name, target_date):
    """Helper to build DailyItineraryResponse."""
    route_items = []
    for r in routes:
        route_items.append(RouteResponse(
            route_id=r.route_id,
            supervisor_id=r.supervisor_id,
            supervisor_name=supervisor_name,
            site_id=r.site_id,
            site_name=r.site.name if r.site else None,
            site_address=r.site.address if r.site else None,
            assigned_date=r.assigned_date,
            visit_order=r.visit_order,
            status=r.status,
            created_at=r.created_at,
        ))
    completed = sum(1 for r in routes if r.status == "completed")
    total = len(routes)
    return DailyItineraryResponse(
        supervisor_id=supervisor_id,
        supervisor_name=supervisor_name,
        assigned_date=target_date,
        routes=route_items,
        total_sites=total,
        completed_sites=completed,
        progress_percentage=round(completed / total * 100, 1) if total > 0 else 0.0,
    )


@router.get("/my", response_model=DailyItineraryResponse, summary="Get my today's route")
def get_my_route(
    target_date: date = Query(default=None, description="Date (defaults to today)"),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Get the authenticated supervisor's daily route."""
    if not target_date:
        target_date = date.today()
    routes = RouteService.get_daily_route(db, current_user.user_id, target_date)
    return _build_itinerary(routes, current_user.user_id, current_user.name, target_date)


@router.get("/supervisor/{supervisor_id}", response_model=DailyItineraryResponse, summary="Get supervisor's route")
def get_supervisor_route(
    supervisor_id: str,
    target_date: date = Query(default=None),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get a specific supervisor's daily route."""
    if not target_date:
        target_date = date.today()
    routes = RouteService.get_daily_route(db, supervisor_id, target_date)
    supervisor = db.query(User).filter(User.user_id == supervisor_id).first()
    name = supervisor.name if supervisor else "Unknown"
    return _build_itinerary(routes, supervisor_id, name, target_date)


@router.get("/date/{target_date}", summary="Get all routes for a date")
def get_routes_for_date(
    target_date: date,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Get all supervisor routes for a specific date."""
    routes = RouteService.get_all_routes_for_date(db, target_date)
    items = []
    for r in routes:
        items.append(RouteResponse(
            route_id=r.route_id,
            supervisor_id=r.supervisor_id,
            supervisor_name=r.supervisor.name if r.supervisor else None,
            site_id=r.site_id,
            site_name=r.site.name if r.site else None,
            site_address=r.site.address if r.site else None,
            assigned_date=r.assigned_date,
            visit_order=r.visit_order,
            status=r.status,
            created_at=r.created_at,
        ))
    return {"routes": items, "total": len(items), "date": target_date.isoformat()}


@router.put("/{route_id}", response_model=RouteResponse, summary="Update route status")
def update_route_status(
    route_id: str,
    status: str = Query(..., description="New status: pending, in_progress, completed, skipped"),
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Update a route assignment's status."""
    try:
        route = RouteService.update_route_status(db, route_id, status)
        return RouteResponse(
            route_id=route.route_id,
            supervisor_id=route.supervisor_id,
            site_id=route.site_id,
            site_name=route.site.name if route.site else None,
            site_address=route.site.address if route.site else None,
            assigned_date=route.assigned_date,
            visit_order=route.visit_order,
            status=route.status,
            created_at=route.created_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)
