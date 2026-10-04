"""
SecureTrack Platform â€” User Schemas (Pydantic v2)
"""
import re
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime

from app.enums import UserRole


# --- Input Schemas ---

class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.GUARD
    phone_number: Optional[str] = Field(None, pattern=r'^\+?[0-9]{7,15}$')
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

    # Roles that self-registrants are allowed to request
    _ALLOWED_SELF_REGISTER_ROLES = {
        UserRole.GUARD, UserRole.SUPERVISOR, UserRole.OUTDOOR,
        UserRole.LEADER, UserRole.LADY, UserRole.PERSONNEL_OFFICER,
        UserRole.OPERATIONS_MANAGER,
    }

    @field_validator("role", mode="before")
    @classmethod
    def restrict_self_register_role(cls, v):
        """SECURITY: Self-registration must not allow privileged roles.
        Any attempt to register as admin/hr/ceo/accountant/client is
        silently downgraded to guard. The admin must elevate the role
        manually during approval.
        """
        # Resolve to UserRole enum for comparison
        try:
            role_enum = UserRole(v) if not isinstance(v, UserRole) else v
        except ValueError:
            return UserRole.GUARD
        privileged = {
            UserRole.ADMIN, UserRole.HR, UserRole.CEO,
            UserRole.ACCOUNTANT, UserRole.CLIENT,
        }
        if role_enum in privileged:
            return UserRole.GUARD
        return role_enum

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
    email: str = Field(..., max_length=255)
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
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole
    phone_number: Optional[str] = Field(None, pattern=r'^\+?[0-9]{7,15}$')
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)
    classification: Optional[str] = Field(None, max_length=50)
    bank_account: Optional[str] = Field(None, max_length=100)
    transfer_name: Optional[str] = Field(None, max_length=255)
    transfer_method: Optional[str] = Field(None, max_length=100)
    payroll_amount: Optional[float] = None

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
    """Admin-level profile update â€” can change any field including role and password."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    phone_number: Optional[str] = None
    email: Optional[str] = Field(None, max_length=255)
    role: Optional[UserRole] = None
    badge_number: Optional[str] = Field(None, max_length=50)
    region: Optional[str] = Field(None, max_length=100)
    new_password: Optional[str] = Field(None, min_length=6, max_length=128)
    is_active: Optional[bool] = None
    classification: Optional[str] = Field(None, max_length=50)
    bank_account: Optional[str] = Field(None, max_length=100)
    base_salary: Optional[float] = None
    transfer_name: Optional[str] = Field(None, max_length=255)
    transfer_method: Optional[str] = Field(None, max_length=100)
    payroll_amount: Optional[float] = None


class HRCompleteProfileRequest(BaseModel):
    """HR completes a 'fresh' user profile."""
    email: str
    password: str = Field(..., min_length=6, max_length=128)
    base_salary: Optional[float] = None
    classification: Optional[str] = Field(None, max_length=50)
    bank_account: Optional[str] = Field(None, max_length=100)
    transfer_name: Optional[str] = Field(None, max_length=255)
    transfer_method: Optional[str] = Field(None, max_length=100)
    national_id: Optional[str] = Field(None, max_length=20)
    insurance_number: Optional[str] = Field(None, max_length=50)
    is_missing_docs: bool = False # If true, status becomes 'missing' instead of 'completed'


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
    onboarding_status: str = "completed"
    base_salary: float = 0.0
    daily_rate: float = 0.0
    payroll_amount: Optional[float] = 0.0
    classification: Optional[str] = None
    employee_code: Optional[str] = None
    bank_account: Optional[str] = None
    transfer_name: Optional[str] = None
    transfer_method: Optional[str] = None
    insurance_status: Optional[str] = None
    insurance_number: Optional[str] = None
    insurance_date: Optional[datetime] = None
    insurable_wage: Optional[float] = 0.0
    hire_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserPublicResponse(BaseModel):
    """Public profile â€” minimal info."""
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
