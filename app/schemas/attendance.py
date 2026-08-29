"""
SecureTrack Platform — Attendance Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import date, datetime

from app.enums import AttendanceStatus


# --- Input Schemas ---

class AttendanceRecord(BaseModel):
    """Record attendance for a single guard."""
    roster_id: str
    status: AttendanceStatus
    replacement_guard_id: Optional[str] = Field(None, description="Guard ID if status is 'replacement'")
    notes: Optional[str] = Field(None, max_length=2000)
    absence_type: Optional[str] = Field(None, description="'excused' or 'unexcused'")
    excused_by: Optional[str] = Field(None)
    overtime_hours: Optional[float] = Field(None)
    overtime_approved_by: Optional[str] = Field(None)
    is_rest_day: bool = False
    is_sick_leave: bool = False
    is_annual_leave: bool = False


class BulkAttendanceRequest(BaseModel):
    """Record attendance for all guards during a visit."""
    visit_id: Optional[str] = None
    records: List[AttendanceRecord]


# --- Output Schemas ---

class AttendanceLogResponse(BaseModel):
    log_id: str
    roster_id: str
    visit_id: str
    supervisor_id: str
    supervisor_name: Optional[str] = None
    guard_id: Optional[str] = None
    guard_name: Optional[str] = None
    guard_badge: Optional[str] = None
    site_name: Optional[str] = None
    status: str
    replacement_guard_id: Optional[str] = None
    replacement_guard_name: Optional[str] = None
    notes: Optional[str] = None
    recorded_at: datetime
    absence_type: Optional[str] = None
    excused_by: Optional[str] = None
    overtime_hours: Optional[float] = None
    overtime_approved_by: Optional[str] = None
    overtime_approved: bool = False
    is_rest_day: bool = False
    is_sick_leave: bool = False
    is_annual_leave: bool = False

    model_config = ConfigDict(from_attributes=True)


class AttendanceListResponse(BaseModel):
    records: List[AttendanceLogResponse]
    total: int


class AttendanceReportResponse(BaseModel):
    """Summary report for a date range."""
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    date_from: date
    date_to: date
    total_scheduled: int = 0
    total_present: int = 0
    total_absent: int = 0
    total_late: int = 0
    total_replacement: int = 0
    attendance_rate: float = 0.0
    records: List[AttendanceLogResponse] = []
