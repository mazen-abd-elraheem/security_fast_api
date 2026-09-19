from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class BonusCreate(BaseModel):
    guard_id: str
    guard_name: str
    guard_code: Optional[str] = None
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    supervisor_name: Optional[str] = None
    shift_time: Optional[str] = None
    amount: float = Field(..., gt=0)
    photo_url: Optional[str] = None
    notes: Optional[str] = None

class BonusUpdate(BaseModel):
    amount: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class BonusOut(BaseModel):
    bonus_id: str
    guard_id: str
    guard_name: str
    guard_code: Optional[str] = None
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    supervisor_name: Optional[str] = None
    shift_time: Optional[str] = None
    amount: float
    photo_url: Optional[str] = None
    status: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None

    class Config:
        from_attributes = True
