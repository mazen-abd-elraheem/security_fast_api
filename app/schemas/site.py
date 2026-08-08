"""
SecureTrack Platform — Site Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

from app.enums import SiteStatus


# --- Input Schemas ---

class SiteCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_meters: int = Field(100, ge=10, le=5000, description="Geofence radius in meters")
    region: Optional[str] = Field(None, max_length=100)


class SiteUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    radius_meters: Optional[int] = Field(None, ge=10, le=5000)
    region: Optional[str] = Field(None, max_length=100)
    status: Optional[SiteStatus] = None


# --- Output Schemas ---

class SiteResponse(BaseModel):
    site_id: str
    name: str
    address: Optional[str] = None
    latitude: float
    longitude: float
    radius_meters: int
    region: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SiteCoverageResponse(BaseModel):
    """Live coverage status for a single site."""
    site_id: str
    site_name: str
    required_guards: int = 0
    present_guards: int = 0
    absent_guards: int = 0
    coverage_percentage: float = 0.0
    last_visit_time: Optional[datetime] = None
    status: str = "unknown"  # green (staffed), yellow (partial), red (shortage)


class SiteListResponse(BaseModel):
    sites: List[SiteResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
