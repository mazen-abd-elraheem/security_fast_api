"""
SecureTrack Platform — Incident Routes
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.incident import IncidentCreate, IncidentUpdate, IncidentResponse, IncidentListResponse
from app.services.incident_service import IncidentService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_audit, log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


@router.post("", response_model=IncidentResponse, status_code=201, summary="Report incident")
def create_incident(
    incident_data: IncidentCreate,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Report a security incident with optional photo evidence."""
    try:
        incident = IncidentService.create_incident(db, current_user.user_id, incident_data)
        return IncidentResponse(
            incident_id=incident.incident_id, site_id=incident.site_id,
            site_name=incident.site.name if incident.site else None,
            reported_by=incident.reported_by, reporter_name=current_user.name,
            visit_id=incident.visit_id, title=incident.title,
            description=incident.description, category=incident.category,
            severity=incident.severity, status=incident.status,
            photo_url=incident.photo_url, created_at=incident.created_at,
            resolved_at=incident.resolved_at, updated_at=incident.updated_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("", response_model=IncidentListResponse, summary="List incidents")
def list_incidents(
    site_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.GUARD, UserRole.OUTDOOR,
    )),
    db: Session = Depends(get_db),
):
    """List all incidents with optional filtering."""
    result = IncidentService.list_incidents(db, site_id, status, severity, skip, limit)
    items = []
    for i in result["incidents"]:
        items.append(IncidentResponse(
            incident_id=i.incident_id, site_id=i.site_id,
            site_name=i.site.name if i.site else None,
            reported_by=i.reported_by,
            reporter_name=i.reporter.name if i.reporter else None,
            visit_id=i.visit_id, title=i.title, description=i.description,
            category=i.category, severity=i.severity, status=i.status,
            photo_url=i.photo_url, created_at=i.created_at,
            resolved_at=i.resolved_at, updated_at=i.updated_at,
        ))
    return IncidentListResponse(incidents=items, total=result["total"])


@router.get("/{incident_id}", response_model=IncidentResponse, summary="Get incident")
def get_incident(
    incident_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN,
    )),
    db: Session = Depends(get_db),
):
    """Get incident details."""
    try:
        i = IncidentService.get_incident(db, incident_id)
        return IncidentResponse(
            incident_id=i.incident_id, site_id=i.site_id,
            site_name=i.site.name if i.site else None,
            reported_by=i.reported_by,
            reporter_name=i.reporter.name if i.reporter else None,
            visit_id=i.visit_id, title=i.title, description=i.description,
            category=i.category, severity=i.severity, status=i.status,
            photo_url=i.photo_url, created_at=i.created_at,
            resolved_at=i.resolved_at, updated_at=i.updated_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/{incident_id}", response_model=IncidentResponse, summary="Update incident")
def update_incident(
    incident_id: str,
    update_data: IncidentUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Update or resolve an incident."""
    try:
        i = IncidentService.update_incident(db, incident_id, update_data)
        return IncidentResponse(
            incident_id=i.incident_id, site_id=i.site_id,
            site_name=i.site.name if i.site else None,
            reported_by=i.reported_by,
            reporter_name=i.reporter.name if i.reporter else None,
            visit_id=i.visit_id, title=i.title, description=i.description,
            category=i.category, severity=i.severity, status=i.status,
            photo_url=i.photo_url, created_at=i.created_at,
            resolved_at=i.resolved_at, updated_at=i.updated_at,
        )
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/site/{site_id}", response_model=IncidentListResponse, summary="Incidents for site")
def get_incidents_for_site(
    site_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR,
    )),
    db: Session = Depends(get_db),
):
    """Get all incidents for a specific site."""
    result = IncidentService.list_incidents(db, site_id=site_id)
    items = []
    for i in result["incidents"]:
        items.append(IncidentResponse(
            incident_id=i.incident_id, site_id=i.site_id,
            site_name=i.site.name if i.site else None,
            reported_by=i.reported_by,
            reporter_name=i.reporter.name if i.reporter else None,
            visit_id=i.visit_id, title=i.title, description=i.description,
            category=i.category, severity=i.severity, status=i.status,
            photo_url=i.photo_url, created_at=i.created_at,
            resolved_at=i.resolved_at, updated_at=i.updated_at,
        ))
    return IncidentListResponse(incidents=items, total=result["total"])
