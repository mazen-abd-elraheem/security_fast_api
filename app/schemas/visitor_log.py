"""
SecureTrack Platform — Visitor Log Schemas
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


# ── Visit Reason ──────────────────────────────────────────────────────────────

class VisitReasonCreate(BaseModel):
    tenant_id: Optional[str] = None
    label: str


class VisitReasonUpdate(BaseModel):
    label: Optional[str] = None
    is_active: Optional[bool] = None


class VisitReasonResponse(BaseModel):
    reason_id: str
    tenant_id: str
    label: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ── Visitor Log ───────────────────────────────────────────────────────────────

class VisitorLogCreate(BaseModel):
    tenant_id: Optional[str] = None
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    visitor_name: str
    visitor_id_number: Optional[str] = None
    visit_reason: str
    visitor_photo_url: Optional[str] = None
    id_photo_url: Optional[str] = None
    notes: Optional[str] = None


class VisitorLogResponse(BaseModel):
    log_id: str
    tenant_id: str
    leader_id: str
    leader_name: str
    site_id: Optional[str]
    site_name: Optional[str]
    visitor_name: str
    visitor_id_number: Optional[str]
    visit_reason: str
    visitor_photo_url: Optional[str]
    id_photo_url: Optional[str]
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class VisitorLogListResponse(BaseModel):
    logs: List[VisitorLogResponse]
    total: int
    page: int
    page_size: int
