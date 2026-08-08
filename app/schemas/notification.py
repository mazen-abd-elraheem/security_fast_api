"""
SecureTrack Platform — Notification Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class NotificationCreate(BaseModel):
    notif_type: str = "system"
    title: str = Field(..., max_length=255)
    message: Optional[str] = None
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None


class SendNotification(BaseModel):
    """Schema for sending a notification to another user."""
    target_user_id: str = Field(..., description="User ID to send the notification to")
    title: str = Field(..., max_length=255)
    message: Optional[str] = None
    notification_type: str = "system"
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None


class NotificationResponse(BaseModel):
    notification_id: str
    user_id: str
    notif_type: str
    title: str
    message: Optional[str] = None
    is_read: bool = False
    reference_id: Optional[str] = None
    reference_type: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    unread_count: int
