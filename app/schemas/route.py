"""
SecureTrack Platform — Supervisor Route Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import date, datetime


# --- Input Schemas ---

class RouteAssignment(BaseModel):
    """Single site in a route."""
    site_id: str
    visit_order: int = Field(1, ge=1)


class RouteCreate(BaseModel):
    """Assign a daily route (list of sites) to a supervisor."""
    supervisor_id: str
    assigned_date: date
    sites: List[RouteAssignment]


# --- Output Schemas ---

class RouteResponse(BaseModel):
    route_id: str
    supervisor_id: str
    supervisor_name: Optional[str] = None
    site_id: str
    site_name: Optional[str] = None
    site_address: Optional[str] = None
    assigned_date: date
    visit_order: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DailyItineraryResponse(BaseModel):
    """Full daily route for a supervisor."""
    supervisor_id: str
    supervisor_name: Optional[str] = None
    assigned_date: date
    routes: List[RouteResponse]
    total_sites: int
    completed_sites: int
    progress_percentage: float = 0.0
