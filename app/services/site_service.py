"""
SecureTrack Platform — Site Service
CRUD operations for security sites with geofence management.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.site import Site
from app.schemas.site import SiteCreate, SiteUpdate
from app.core.exceptions import NotFoundException, DuplicateException


class SiteService:
    """CRUD operations for security sites."""

    @staticmethod
    def create_site(db: Session, site_data: SiteCreate) -> Site:
        """Create a new site with geofence coordinates."""
        db_site = Site(
            site_id=str(uuid.uuid4()),
            name=site_data.name,
            address=site_data.address,
            latitude=site_data.latitude,
            longitude=site_data.longitude,
            radius_meters=site_data.radius_meters,
            region=site_data.region,
        )
        db.add(db_site)
        db.commit()
        db.refresh(db_site)
        return db_site

    @staticmethod
    def get_site(db: Session, site_id: str) -> Site:
        """Get a site by ID."""
        site = db.query(Site).filter(Site.site_id == site_id).first()
        if not site:
            raise NotFoundException("Site", site_id)
        return site

    @staticmethod
    def update_site(db: Session, site_id: str, update_data: SiteUpdate) -> Site:
        """Update site details including geofence."""
        site = SiteService.get_site(db, site_id)

        if update_data.name is not None:
            site.name = update_data.name
        if update_data.address is not None:
            site.address = update_data.address
        if update_data.latitude is not None:
            site.latitude = update_data.latitude
        if update_data.longitude is not None:
            site.longitude = update_data.longitude
        if update_data.radius_meters is not None:
            site.radius_meters = update_data.radius_meters
        if update_data.region is not None:
            site.region = update_data.region
        if update_data.status is not None:
            site.status = update_data.status.value

        db.commit()
        db.refresh(site)
        return site

    @staticmethod
    def list_sites(
        db: Session,
        region: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """List sites with optional filtering."""
        query = db.query(Site)

        if region:
            query = query.filter(Site.region == region)
        if status:
            query = query.filter(Site.status == status)

        total = query.count()
        sites = query.order_by(Site.name.asc()).offset(skip).limit(limit).all()

        return {
            "sites": sites,
            "total": total,
            "page": (skip // limit) + 1 if limit > 0 else 1,
            "page_size": limit,
            "total_pages": max(1, (total + limit - 1) // limit) if limit > 0 else 1,
        }

    @staticmethod
    def delete_site(db: Session, site_id: str) -> None:
        """Deactivate a site (soft delete)."""
        site = SiteService.get_site(db, site_id)
        site.status = "inactive"
        db.commit()
