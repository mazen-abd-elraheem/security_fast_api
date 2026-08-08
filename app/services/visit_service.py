"""
SecureTrack Platform — Visit Service (Core Geofence Engine)
GPS-verified check-in/check-out with geofence validation.
This is the most critical service in the system.
"""
import uuid
import logging
from typing import Optional
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.supervisor_visit import SupervisorVisit
from app.models.supervisor_route import SupervisorRoute
from app.models.site import Site
from app.models.user import User
from app.services.geo_service import GeoService
from app.core.exceptions import (
    NotFoundException,
    BadRequestException,
    GeofenceViolationException,
)

log = logging.getLogger(__name__)


class VisitService:
    """GPS-verified check-in/check-out with geofence validation."""

    @staticmethod
    def check_in(
        db: Session,
        supervisor_id: str,
        site_id: str,
        latitude: float,
        longitude: float,
        photo_url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> SupervisorVisit:
        """
        Geofenced check-in at a site.

        1. Validate site exists
        2. Calculate distance from site center
        3. Reject if outside geofence radius
        4. Find matching route assignment
        5. Create verified visit record
        6. Update route status to in_progress
        """
        # 1. Validate site
        site = db.query(Site).filter(Site.site_id == site_id).first()
        if not site:
            raise NotFoundException("Site", site_id)

        if site.status != "active":
            raise BadRequestException(f"Site '{site.name}' is not active")

        # 2-3. Geofence validation
        is_within, distance_m = GeoService.is_within_geofence(
            site.latitude, site.longitude,
            latitude, longitude,
            site.radius_meters,
        )

        if not is_within:
            log.warning(
                "Geofence violation: supervisor=%s, site=%s, distance=%.1fm, required=%dm",
                supervisor_id, site_id, distance_m, site.radius_meters,
            )
            raise GeofenceViolationException(distance_m, site.radius_meters)

        # Check for duplicate active visit (already checked in, not checked out)
        active_visit = db.query(SupervisorVisit).filter(
            SupervisorVisit.supervisor_id == supervisor_id,
            SupervisorVisit.site_id == site_id,
            SupervisorVisit.check_out_time.is_(None),
        ).first()
        if active_visit:
            raise BadRequestException("You are already checked in at this site")

        # 4. Find matching route (optional — visits can happen without a pre-assigned route)
        today = date.today()
        route = db.query(SupervisorRoute).filter(
            SupervisorRoute.supervisor_id == supervisor_id,
            SupervisorRoute.site_id == site_id,
            SupervisorRoute.assigned_date == today,
        ).first()

        # 5. Create visit record
        now = datetime.now(timezone.utc)
        db_visit = SupervisorVisit(
            visit_id=str(uuid.uuid4()),
            supervisor_id=supervisor_id,
            site_id=site_id,
            route_id=route.route_id if route else None,
            check_in_time=now,
            check_in_lat=latitude,
            check_in_lng=longitude,
            distance_from_site=distance_m,
            is_verified=True,
            photo_url=photo_url,
            notes=notes,
        )
        db.add(db_visit)

        # 6. Update route status
        if route:
            route.status = "in_progress"

        log.info(
            "Check-in verified: supervisor=%s, site=%s (%s), distance=%.1fm",
            supervisor_id, site.name, site_id, distance_m,
        )

        db.commit()
        db.refresh(db_visit)
        return db_visit

    @staticmethod
    def check_out(
        db: Session,
        visit_id: str,
        supervisor_id: str,
        latitude: float,
        longitude: float,
        notes: Optional[str] = None,
    ) -> SupervisorVisit:
        """Check out from a site visit."""
        visit = db.query(SupervisorVisit).filter(
            SupervisorVisit.visit_id == visit_id,
        ).first()
        if not visit:
            raise NotFoundException("Visit", visit_id)
        if visit.supervisor_id != supervisor_id:
            raise BadRequestException("This is not your visit")
        if visit.check_out_time is not None:
            raise BadRequestException("Already checked out")

        now = datetime.now(timezone.utc)
        visit.check_out_time = now
        visit.check_out_lat = latitude
        visit.check_out_lng = longitude

        if notes:
            existing_notes = visit.notes or ""
            visit.notes = f"{existing_notes}\n[Checkout] {notes}".strip()

        # Update route status to completed
        if visit.route_id:
            route = db.query(SupervisorRoute).filter(
                SupervisorRoute.route_id == visit.route_id,
            ).first()
            if route:
                route.status = "completed"

        log.info("Check-out: visit=%s, supervisor=%s", visit_id, supervisor_id)

        db.commit()
        db.refresh(visit)
        return visit

    @staticmethod
    def get_visits_for_supervisor(
        db: Session,
        supervisor_id: str,
        target_date: Optional[date] = None,
    ) -> list:
        """Get all visits for a supervisor, optionally filtered by date."""
        query = db.query(SupervisorVisit).filter(
            SupervisorVisit.supervisor_id == supervisor_id,
        )
        if target_date:
            query = query.filter(
                SupervisorVisit.check_in_time >= datetime.combine(target_date, datetime.min.time()),
                SupervisorVisit.check_in_time < datetime.combine(target_date, datetime.max.time()),
            )
        return query.order_by(SupervisorVisit.check_in_time.desc()).all()

    @staticmethod
    def get_visits_for_site(
        db: Session,
        site_id: str,
        target_date: Optional[date] = None,
    ) -> list:
        """Get all visits for a site, optionally filtered by date."""
        query = db.query(SupervisorVisit).filter(
            SupervisorVisit.site_id == site_id,
        )
        if target_date:
            query = query.filter(
                SupervisorVisit.check_in_time >= datetime.combine(target_date, datetime.min.time()),
                SupervisorVisit.check_in_time < datetime.combine(target_date, datetime.max.time()),
            )
        return query.order_by(SupervisorVisit.check_in_time.desc()).all()
