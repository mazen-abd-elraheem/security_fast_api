"""
SecureTrack Platform — Uniform Schemas
Pydantic schemas for uniform item CRUD operations.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

from app.enums import UniformItemType, UniformStatus


# --- Input Schemas ---

class UniformItemCreate(BaseModel):
    employee_id: str
    item_type: UniformItemType
    size: Optional[str] = Field(None, max_length=20)
    color: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=500)
    issued_date: Optional[datetime] = None


class UniformItemUpdate(BaseModel):
    item_type: Optional[UniformItemType] = None
    size: Optional[str] = Field(None, max_length=20)
    color: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=500)
    status: Optional[UniformStatus] = None
    returned_date: Optional[datetime] = None


class UniformBulkIssue(BaseModel):
    """Issue multiple uniform items to one employee at once."""
    employee_id: str
    items: List[UniformItemCreate]


# --- Output Schemas ---

class UniformItemResponse(BaseModel):
    item_id: str
    employee_id: str
    employee_name: Optional[str] = None
    item_type: str
    size: Optional[str] = None
    color: Optional[str] = None
    notes: Optional[str] = None
    status: str
    issued_date: datetime
    returned_date: Optional[datetime] = None
    issued_by: Optional[str] = None
    issuer_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UniformItemListResponse(BaseModel):
    items: List[UniformItemResponse]
    total: int


class EmployeeUniformSummary(BaseModel):
    """Summary of one employee's uniform status."""
    employee_id: str
    employee_name: str
    badge_number: Optional[str] = None
    role: str
    is_active: bool
    total_items_issued: int
    total_items_returned: int
    total_items_outstanding: int  # issued - returned
    items: List[UniformItemResponse]


class UniformTrackerResponse(BaseModel):
    """Admin tracker view: employees missing uniforms + terminated with unreturned."""
    employees_without_uniform: List[EmployeeUniformSummary]
    terminated_with_unreturned: List[EmployeeUniformSummary]
    total_without_uniform: int
    total_terminated_unreturned: int
