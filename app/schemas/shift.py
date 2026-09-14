"""
SecureTrack Platform — Shift Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import time, datetime


# --- Input Schemas ---

class ShiftCreate(BaseModel):
    site_id: str
    start_time: time
    end_time: time
    days_of_week: str = Field("mon,tue,wed,thu,fri,sat,sun", max_length=100,
                              description="Comma-separated days: mon,tue,wed,thu,fri,sat,sun")
    required_headcount: int = Field(1, ge=1, le=100)
    label: Optional[str] = Field(None, max_length=100, description="E.g. 'Morning Shift', 'Night Watch'")


class ShiftUpdate(BaseModel):
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    days_of_week: Optional[str] = Field(None, max_length=100)
    required_headcount: Optional[int] = Field(None, ge=1, le=100)
    label: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


# --- Output Schemas ---

class ShiftResponse(BaseModel):
    shift_id: str
    site_id: str
    site_name: Optional[str] = None
    start_time: time
    end_time: time
    days_of_week: str
    required_headcount: int
    label: Optional[str] = None
    is_active: bool

    # Tactical Details (Dashboard UI)
    location_tag: Optional[str] = None
    checkpoints_scheduled: int
    compliance_gauge: float
    armed_standard: Optional[str] = None
    rfid_perimeter_status: Optional[str] = None
    vehicles_assigned: int
    sector_loops: int
    authorization_protocol: Optional[str] = None
    compliance_certification: Optional[str] = None
    shift_status_override: Optional[str] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShiftListResponse(BaseModel):
    shifts: List[ShiftResponse]
    total: int
