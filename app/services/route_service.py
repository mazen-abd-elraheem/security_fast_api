"""
SecureTrack Platform â€” Route Service
Manages daily supervisor route assignments.
"""
import uuid
from typing import Optional, List
from datetime import date

from sqlalchemy.orm import Session

from app.models.supervisor_route import SupervisorRoute
from app.models.site import Site
from app.models.user import User
from app.schemas.route import RouteCreate
from app.core.exceptions import NotFoundException, BadRequestException, DuplicateException


class RouteService:
    """Manages daily supervisor route assignments."""

    @staticmethod
    def assign_route(db: Session, route_data: RouteCreate) -> List[SupervisorRoute]:
        """Assign a daily route (list of sites) to a supervisor."""
        # Validate supervisor
        supervisor = db.query(User).filter(User.user_id == route_data.supervisor_id).first()
        if not supervisor:
            raise NotFoundException("Supervisor", route_data.supervisor_id)
        if supervisor.role not in ("supervisor", "leader"):
            raise BadRequestException(f"User {supervisor.name} is not a supervisor or leader")

        routes = []
        for site_assignment in route_data.sites:
            # Validate site
            site = db.query(Site).filter(Site.site_id == site_assignment.site_id).first()
            if not site:
                raise NotFoundException("Site", site_assignment.site_id)

            # Check for duplicate
            existing = db.query(SupervisorRoute).filter(
                SupervisorRoute.supervisor_id == route_data.supervisor_id,
                SupervisorRoute.site_id == site_assignment.site_id,
                SupervisorRoute.assigned_date == route_data.assigned_date,
            ).first()
            if existing:
                raise DuplicateException(
                    f"Supervisor already assigned to site '{site.name}' on {route_data.assigned_date}"
                )

            db_route = SupervisorRoute(
                route_id=str(uuid.uuid4()),
                supervisor_id=route_data.supervisor_id,
                site_id=site_assignment.site_id,
                shift_id=site_assignment.shift_id,
                assigned_date=route_data.assigned_date,
                visit_order=site_assignment.visit_order,
            )
            db.add(db_route)
            routes.append(db_route)

        db.commit()
        for r in routes:
            db.refresh(r)
        return routes

    @staticmethod
    def bulk_assign_route(db: Session, supervisor_id: str, dates: list, sites: list) -> list:
        """Assign a supervisor to the same sites across multiple dates."""
        supervisor = db.query(User).filter(User.user_id == supervisor_id).first()
        if not supervisor:
            raise NotFoundException("Supervisor", supervisor_id)
        if supervisor.role not in ("supervisor", "leader"):
            raise BadRequestException(f"User {supervisor.name} is not a supervisor or leader")

        # Batch-load all sites to avoid N+1 queries inside the loop
        site_ids = {s.site_id for s in sites}
        site_objs = db.query(Site).filter(Site.site_id.in_(site_ids)).all()
        site_map = {s.site_id: s for s in site_objs}
        for sid in site_ids:
            if sid not in site_map:
                raise NotFoundException("Site", sid)

        # Batch-load existing routes for this supervisor in the date range (ONE query instead of N)
        existing_routes = db.query(
            SupervisorRoute.assigned_date,
            SupervisorRoute.site_id,
            SupervisorRoute.shift_id,
        ).filter(
            SupervisorRoute.supervisor_id == supervisor_id,
            SupervisorRoute.site_id.in_(site_ids),
            SupervisorRoute.assigned_date.in_(dates),
        ).all()

        # Build a set of (date, site_id, shift_id) for O(1) duplicate detection
        existing_set = {(r.assigned_date, r.site_id, r.shift_id) for r in existing_routes}

        all_routes = []
        for target_date in dates:
            for site_assignment in sites:
                key = (target_date, site_assignment.site_id, site_assignment.shift_id)
                if key in existing_set:
                    continue

                db_route = SupervisorRoute(
                    route_id=str(uuid.uuid4()),
                    supervisor_id=supervisor_id,
                    site_id=site_assignment.site_id,
                    shift_id=site_assignment.shift_id,
                    assigned_date=target_date,
                    visit_order=site_assignment.visit_order,
                )
                db.add(db_route)
                all_routes.append(db_route)

        db.commit()
        return all_routes


    @staticmethod
    def get_daily_route(db: Session, supervisor_id: str, target_date: date) -> list:
        """Get a supervisor's daily route."""
        return (
            db.query(SupervisorRoute)
            .filter(
                SupervisorRoute.supervisor_id == supervisor_id,
                SupervisorRoute.assigned_date == target_date,
            )
            .order_by(SupervisorRoute.visit_order.asc())
            .all()
        )

    @staticmethod
    def get_all_routes_for_date(db: Session, target_date: date) -> list:
        """Get all supervisor routes for a specific date."""
        return (
            db.query(SupervisorRoute)
            .filter(SupervisorRoute.assigned_date == target_date)
            .order_by(SupervisorRoute.supervisor_id, SupervisorRoute.visit_order.asc())
            .all()
        )

    @staticmethod
    def update_route_status(db: Session, route_id: str, status: str) -> SupervisorRoute:
        """Update a route's status."""
        route = db.query(SupervisorRoute).filter(SupervisorRoute.route_id == route_id).first()
        if not route:
            raise NotFoundException("Route", route_id)
        route.status = status
        db.commit()
        db.refresh(route)
        return route
