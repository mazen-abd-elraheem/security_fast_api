"""
SecureTrack Platform — Device Registry Schemas
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime


# --- Input Schemas ---

class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(..., max_length=255, description="IMEI or unique device identifier")
    device_model: Optional[str] = Field(None, max_length=255, description="E.g. 'Samsung Galaxy S23'")
    os_version: Optional[str] = Field(None, max_length=100, description="E.g. 'Android 14'")


# --- Output Schemas ---

class DeviceResponse(BaseModel):
    registry_id: str
    user_id: str
    device_id: str
    device_model: Optional[str] = None
    os_version: Optional[str] = None
    is_trusted: bool = True
    registered_at: datetime
    last_seen_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DeviceListResponse(BaseModel):
    devices: List[DeviceResponse]
    total: int
