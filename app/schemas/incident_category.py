"""
SecureTrack Platform — Incident Category Schemas
"""
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime


class IncidentCategoryCreate(BaseModel):
    name: str
    name_ar: Optional[str] = None
    severity: str = "medium"  # low / medium / high / critical
    corrective_action: Optional[str] = None
    alert_roles: List[str] = []


class IncidentCategoryUpdate(BaseModel):
    name: Optional[str] = None
    name_ar: Optional[str] = None
    severity: Optional[str] = None
    corrective_action: Optional[str] = None
    alert_roles: Optional[List[str]] = None
    is_active: Optional[bool] = None


class IncidentCategoryResponse(BaseModel):
    category_id: str
    name: str
    name_ar: Optional[str] = None
    severity: str
    corrective_action: Optional[str] = None
    alert_roles: List[str] = []
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentCategoryListResponse(BaseModel):
    categories: List[IncidentCategoryResponse]
    total: int
