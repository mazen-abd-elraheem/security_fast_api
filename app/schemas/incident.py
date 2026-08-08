"""
SecureTrack Platform — Incident Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

from app.enums import IncidentCategory, IncidentSeverity, IncidentStatus


# --- Input Schemas ---

class IncidentCreate(BaseModel):
    site_id: str
    visit_id: Optional[str] = None
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    category: IncidentCategory = IncidentCategory.OTHER
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    photo_url: Optional[str] = None


class IncidentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    category: Optional[IncidentCategory] = None
    severity: Optional[IncidentSeverity] = None
    status: Optional[IncidentStatus] = None
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
    category: str
    severity: str
    status: str
    photo_url: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentListResponse(BaseModel):
    incidents: List[IncidentResponse]
    total: int
