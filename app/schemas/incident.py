"""
SecureTrack Platform — Incident Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime


# --- Input Schemas ---

class IncidentCreate(BaseModel):
    site_id: str
    visit_id: Optional[str] = None
    # category_id now references a dynamic IncidentCategory (UUID)
    category_id: str
    description: Optional[str] = Field(None, max_length=5000)
    photo_url: Optional[str] = None
    # title and severity are auto-derived from the category — not provided by reporter


class IncidentUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[str] = None   # open / investigating / resolved / closed
    severity: Optional[str] = None  # admin can override severity
    photo_url: Optional[str] = None


# --- Output Schemas ---

class IncidentResponse(BaseModel):
    incident_id: str
    site_id: str
    site_name: Optional[str] = None
    reported_by: str
    reporter_name: Optional[str] = None
    visit_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    category: str          # stores category name for display
    category_id: Optional[str] = None  # UUID of the IncidentCategory
    severity: str
    status: str
    photo_url: Optional[str] = None
    corrective_action: Optional[str] = None  # returned after create
    created_at: datetime
    resolved_at: Optional[datetime] = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentListResponse(BaseModel):
    incidents: List[IncidentResponse]
    total: int
