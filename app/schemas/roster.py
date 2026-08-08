"""
SecureTrack Platform — Guard Roster Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import date, datetime


# --- Input Schemas ---

class RosterCreate(BaseModel):
    guard_id: str
    shift_id: str
    assigned_date: date


class BulkRosterCreate(BaseModel):
    """Assign multiple guards to shifts at once."""
    assignments: List[RosterCreate]


# --- Output Schemas ---

class RosterResponse(BaseModel):
    roster_id: str
    guard_id: str
    guard_name: Optional[str] = None
    guard_badge: Optional[str] = None
    shift_id: str
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    shift_label: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    assigned_date: date
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RosterListResponse(BaseModel):
    roster: List[RosterResponse]
    total: int
