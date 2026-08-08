"""
SecureTrack Platform — Supervisor Visit Schemas (Core Geofence Engine)
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime


# --- Input Schemas ---

class CheckInRequest(BaseModel):
    """GPS-verified check-in at a site."""
    site_id: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    photo_url: Optional[str] = Field(None, description="Photo proof URL (optional)")
    notes: Optional[str] = Field(None, max_length=2000)


class CheckOutRequest(BaseModel):
    """Check-out from a site visit."""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    notes: Optional[str] = Field(None, max_length=2000)


# --- Output Schemas ---

class VisitResponse(BaseModel):
    visit_id: str
    supervisor_id: str
    supervisor_name: Optional[str] = None
    site_id: str
    site_name: Optional[str] = None
    route_id: Optional[str] = None
    check_in_time: datetime
    check_in_lat: float
    check_in_lng: float
    distance_from_site: Optional[float] = None
    check_out_time: Optional[datetime] = None
    check_out_lat: Optional[float] = None
    check_out_lng: Optional[float] = None
    is_verified: bool = True
    photo_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VisitListResponse(BaseModel):
    visits: List[VisitResponse]
    total: int
