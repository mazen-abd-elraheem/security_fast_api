"""
SecureTrack Platform — User Schemas (Pydantic v2)
"""
import re
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime

from app.enums import UserRole


# --- Input Schemas ---

class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.GUARD
    phone_number: Optional[str] = Field(None, pattern=r'^\+?[0-9]{7,15}$')
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r'[a-z]', v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r'[0-9]', v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
            raise ValueError("Password must contain at least one special character")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone_number: Optional[str] = Field(None, pattern=r'^\+?[0-9]{7,15}$')
    profile_image_url: Optional[str] = None
    region: Optional[str] = Field(None, max_length=100)


class UserLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class AdminUserCreate(BaseModel):
    """Admin creates any type of user account."""
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole
    phone_number: Optional[str] = Field(None, pattern=r'^\+?[0-9]{7,15}$')
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r'[A-Z]', v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r'[a-z]', v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r'[0-9]', v):
            raise ValueError("Password must contain at least one digit")
        return v


class AdminUserUpdate(BaseModel):
    """Admin-level profile update — can change any field including role and password."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)
    new_password: Optional[str] = Field(None, min_length=6, max_length=128)
    is_active: Optional[bool] = None


# --- Output Schemas ---

class UserResponse(BaseModel):
    user_id: str
    name: str
    email: str
    phone_number: Optional[str] = None
    role: str
    badge_number: Optional[str] = None
    region: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    profile_image_url: Optional[str] = None
    is_active: bool = True
    status: Optional[str] = "active"
    requested_role: Optional[str] = None
    base_salary: float = 0.0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPublicResponse(BaseModel):
    """Public profile — minimal info."""
    user_id: str
    name: str
    role: str
    badge_number: Optional[str] = None
    region: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    users: List[UserResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
