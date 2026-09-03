"""
SecureTrack Platform — Site Routes
Site CRUD with geofence management.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.site import SiteCreate, SiteUpdate, SiteResponse, SiteListResponse
from app.services.site_service import SiteService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


@router.post("", response_model=SiteResponse, status_code=201, summary="Create a site")
def create_site(
    site_data: SiteCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Create a new site with geofence coordinates."""
    try:
        site = SiteService.create_site(db, site_data)
        log_create(db, current_user, "site", site)
        db.commit()
        return site
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("", response_model=SiteListResponse, summary="List all sites")
def list_sites(
    region: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.GUARD, UserRole.OPERATIONS_MANAGER, UserRole.LEADER,
    )),
    db: Session = Depends(get_db),
):
    """List all sites with optional filtering."""
    return SiteService.list_sites(db, region=region, status=status, skip=skip, limit=limit)


@router.get("/{site_id}", response_model=SiteResponse, summary="Get site details")
def get_site(
    site_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER, UserRole.LEADER,
    )),
    db: Session = Depends(get_db),
):
    """Get site details including geofence configuration."""
    try:
        return SiteService.get_site(db, site_id)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/{site_id}", response_model=SiteResponse, summary="Update site")
def update_site(
    site_id: str,
    update_data: SiteUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Update site details and geofence configuration."""
    try:
        old_site = SiteService.get_site(db, site_id)
        old = snapshot(old_site)
        updated = SiteService.update_site(db, site_id, update_data)
        log_update(db, current_user, "site", old, updated)
        db.commit()
        return updated
    except SecureTrackException as e:
        handle_service_exception(e)


@router.delete("/{site_id}", status_code=200, summary="Deactivate site")
def delete_site(
    site_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Deactivate a site (soft delete)."""
    try:
        site = SiteService.get_site(db, site_id)
        log_delete(db, current_user, "site", site)
        SiteService.delete_site(db, site_id)
        db.commit()
        return {"detail": "Site deactivated"}
    except SecureTrackException as e:
        handle_service_exception(e)
