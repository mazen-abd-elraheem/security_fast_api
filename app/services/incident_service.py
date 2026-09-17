"""
SecureTrack Platform — Incident Service
Manages security incident reports with dynamic category lookup and alert notifications.
"""
import uuid
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.incident_category import IncidentCategory
from app.models.site import Site
from app.models.user import User
from app.schemas.incident import IncidentCreate, IncidentUpdate
from app.core.exceptions import NotFoundException, BadRequestException


def _send_incident_alerts(db: Session, incident: Incident, category: IncidentCategory):
    """Create in-app notification records for all alert roles of this category."""
    try:
        from app.api.v1.notifications import create_notification
        alert_roles = category.alert_roles  # list of role strings
        if not alert_roles:
            return

        recipients = db.query(User).filter(
            User.role.in_(alert_roles),
            User.is_active == True,
        ).all()

        severity_label = category.severity.upper()
        for user in recipients:
            create_notification(
                db,
                user_id=user.user_id,
                notif_type="incident_reported",
                title=f"🚨 [{severity_label}] {incident.title}",
                message=f"Incident reported at {incident.site.name if incident.site else 'unknown site'}. Category: {category.name}.",
                reference_id=incident.incident_id,
                reference_type="incident",
            )
        db.commit()
    except Exception as e:
        # Non-fatal — log and continue
        import logging
        logging.getLogger(__name__).warning(f"Failed to send incident alerts: {e}")


class IncidentService:
    """Manages security incident reports."""

    @staticmethod
    def create_incident(db: Session, reporter_id: str, incident_data: IncidentCreate) -> Incident:
        """Create a new incident report with auto-derived title and severity from category."""
        # Validate site
        site = db.query(Site).filter(Site.site_id == incident_data.site_id).first()
        if not site:
            raise NotFoundException("Site", incident_data.site_id)

        # Look up the dynamic category
        category = db.query(IncidentCategory).filter(
            IncidentCategory.category_id == incident_data.category_id,
            IncidentCategory.is_active == True,
        ).first()
        if not category:
            raise BadRequestException(f"Incident category '{incident_data.category_id}' not found or inactive.")

        db_incident = Incident(
            incident_id=str(uuid.uuid4()),
            site_id=incident_data.site_id,
            reported_by=reporter_id,
            visit_id=incident_data.visit_id,
            title=category.name,          # auto-derived from category
            description=incident_data.description,
            category_id=category.category_id,
            category=category.name,       # denormalized
            severity=category.severity,   # auto-derived from category
            photo_url=incident_data.photo_url,
        )
        db.add(db_incident)
        db.commit()
        db.refresh(db_incident)

        # Send alert notifications to designated roles
        _send_incident_alerts(db, db_incident, category)

        return db_incident

    @staticmethod
    def get_incident(db: Session, incident_id: str) -> Incident:
        """Get an incident by ID."""
        incident = db.query(Incident).filter(Incident.incident_id == incident_id).first()
        if not incident:
            raise NotFoundException("Incident", incident_id)
        return incident

    @staticmethod
    def update_incident(db: Session, incident_id: str, update_data: IncidentUpdate) -> Incident:
        """Update incident details (admin)."""
        incident = IncidentService.get_incident(db, incident_id)

        if update_data.description is not None:
            incident.description = update_data.description
        if update_data.severity is not None:
            incident.severity = update_data.severity
        if update_data.status is not None:
            incident.status = update_data.status
            if update_data.status == "resolved":
                incident.resolved_at = datetime.now(timezone.utc)
        if update_data.photo_url is not None:
            incident.photo_url = update_data.photo_url

        db.commit()
        db.refresh(incident)
        return incident

    @staticmethod
    def list_incidents(
        db: Session,
        site_id: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        reported_by_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """List incidents with optional filtering."""
        query = db.query(Incident)
        if site_id:
            query = query.filter(Incident.site_id == site_id)
        if status:
            query = query.filter(Incident.status == status)
        if severity:
            query = query.filter(Incident.severity == severity)
        if reported_by_id:
            query = query.filter(Incident.reported_by == reported_by_id)

        total = query.count()
        incidents = query.order_by(Incident.created_at.desc()).offset(skip).limit(limit).all()

        return {"incidents": incidents, "total": total}

    @staticmethod
    def resolve_incident(db: Session, incident_id: str) -> Incident:
        """Mark an incident as resolved."""
        incident = IncidentService.get_incident(db, incident_id)
        incident.status = "resolved"
        incident.resolved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(incident)
        return incident
