"""
SecureTrack Platform — Incident Service
Manages security incident reports with photo evidence.
"""
import uuid
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.site import Site
from app.schemas.incident import IncidentCreate, IncidentUpdate
from app.core.exceptions import NotFoundException


class IncidentService:
    """Manages security incident reports."""

    @staticmethod
    def create_incident(db: Session, reporter_id: str, incident_data: IncidentCreate) -> Incident:
        """Create a new incident report."""
        site = db.query(Site).filter(Site.site_id == incident_data.site_id).first()
        if not site:
            raise NotFoundException("Site", incident_data.site_id)

        db_incident = Incident(
            incident_id=str(uuid.uuid4()),
            site_id=incident_data.site_id,
            reported_by=reporter_id,
            visit_id=incident_data.visit_id,
            title=incident_data.title,
            description=incident_data.description,
            category=incident_data.category.value,
            severity=incident_data.severity.value,
            photo_url=incident_data.photo_url,
        )
        db.add(db_incident)
        db.commit()
        db.refresh(db_incident)
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
        """Update incident details."""
        incident = IncidentService.get_incident(db, incident_id)

        if update_data.title is not None:
            incident.title = update_data.title
        if update_data.description is not None:
            incident.description = update_data.description
        if update_data.category is not None:
            incident.category = update_data.category.value
        if update_data.severity is not None:
            incident.severity = update_data.severity.value
        if update_data.status is not None:
            incident.status = update_data.status.value
            if update_data.status.value == "resolved":
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
