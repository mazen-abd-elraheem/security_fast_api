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
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.GUARD, UserRole.OPERATIONS_MANAGER, UserRole.LEADER, UserRole.HR,
    )),
    db: Session = Depends(get_db),
):
    """List all sites with optional filtering."""
    return SiteService.list_sites(db, region=region, status=status, skip=skip, limit=limit)


@router.get("/{site_id}", response_model=SiteResponse, summary="Get site details")
def get_site(
    site_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR, UserRole.OPERATIONS_MANAGER, UserRole.LEADER, UserRole.HR,
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


@router.post("/import-bulk", summary="Bulk import / upsert sites from CSV")
def import_bulk_sites(
    rows: list[dict],
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """
    Bulk upsert sites from a CSV export.
    Lookup key: site name (case-insensitive).
    - Existing site  → smart-update only changed fields.
    - Not found      → create (requires lat/lon).
    """
    from app.models.site import Site
    import uuid as _uuid

    def _sf(v):
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    def _si(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    def _ss(v):
        return str(v).strip() if v not in (None, "", "null", "None") else ""

    created = updated = skipped = 0

    for row in rows:
        name = _ss(row.get("name") or row.get("site_name") or row.get("Site Name", ""))
        if not name:
            skipped += 1
            continue

        address    = _ss(row.get("address")        or row.get("Address",       ""))
        region     = _ss(row.get("region")          or row.get("Region",        ""))
        lat        = _sf(row.get("latitude")        or row.get("Latitude"))
        lon        = _sf(row.get("longitude")       or row.get("Longitude"))
        radius     = _si(row.get("radius_meters")   or row.get("Geofence (m)"))
        status_raw = _ss(row.get("status")          or row.get("Status",   "active")).lower()
        is_base_raw = str(row.get("is_base", "") or row.get("Is Base", "")).strip().lower()
        is_base    = is_base_raw in ("true", "1", "yes")

        valid_statuses = {"active", "inactive", "maintenance"}
        status = status_raw if status_raw in valid_statuses else "active"

        existing = db.query(Site).filter(Site.name.ilike(name)).first()

        if existing:
            changed = False
            if address and (existing.address or "") != address:
                existing.address = address; changed = True
            if region  and (existing.region  or "") != region:
                existing.region  = region;  changed = True
            if lat  is not None and abs((existing.latitude  or 0) - lat)  > 0.000001:
                existing.latitude  = lat;   changed = True
            if lon  is not None and abs((existing.longitude or 0) - lon)  > 0.000001:
                existing.longitude = lon;   changed = True
            if radius is not None and existing.radius_meters != radius:
                existing.radius_meters = radius; changed = True
            if status and existing.status != status:
                existing.status = status;   changed = True
            if is_base_raw and existing.is_base != is_base:
                existing.is_base = is_base; changed = True

            if changed:
                db.flush()
                updated += 1
            else:
                skipped += 1
        else:
            if lat is None or lon is None:
                skipped += 1
                continue

            db.add(Site(
                site_id=str(_uuid.uuid4()),
                name=name,
                address=address or None,
                region=region or None,
                latitude=lat,
                longitude=lon,
                radius_meters=radius or 100,
                status=status,
                is_base=is_base,
            ))
            db.flush()
            created += 1

    db.commit()
    return {
        "detail": f"Sites import: {created} created, {updated} updated, {skipped} skipped",
        "created_count": created,
        "updated_count": updated,
        "skipped_count": skipped,
        "total_count": len(rows),
    }
