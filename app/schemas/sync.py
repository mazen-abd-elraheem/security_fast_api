"""
SecureTrack Platform — Offline Sync Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SyncVisitRecord(BaseModel):
    """A check-in record cached offline."""
    site_id: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    check_in_time: datetime
    check_out_time: Optional[datetime] = None
    photo_url: Optional[str] = None
    notes: Optional[str] = None


class SyncAttendanceRecord(BaseModel):
    """An attendance record cached offline."""
    roster_id: str
    status: str
    replacement_guard_id: Optional[str] = None
    notes: Optional[str] = None
    recorded_at: datetime


class SyncPushRequest(BaseModel):
    """Push cached offline data to server."""
    visits: List[SyncVisitRecord] = []
    attendance_records: List[SyncAttendanceRecord] = []
    device_id: Optional[str] = None


class SyncResultItem(BaseModel):
    index: int
    record_type: str  # "visit" or "attendance"
    success: bool
    error: Optional[str] = None
    created_id: Optional[str] = None


class SyncStatusResponse(BaseModel):
    total_received: int = 0
    total_processed: int = 0
    total_failed: int = 0
    results: List[SyncResultItem] = []
