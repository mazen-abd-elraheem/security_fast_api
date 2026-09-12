"""
SecureTrack Platform — Task Inspection & Alert System Schemas
Pydantic models for request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ══════════════════════════════════════════════
# Tenant Schemas
# ══════════════════════════════════════════════

class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

class TenantUpdate(BaseModel):
    name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    status: Optional[str] = None
    logo_url: Optional[str] = None

class TenantOut(BaseModel):
    tenant_id: str
    name: str
    tenant_code: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    status: str
    logo_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TenantSiteAccessCreate(BaseModel):
    site_id: str


# ══════════════════════════════════════════════
# Client Account Schemas
# ══════════════════════════════════════════════

class ClientAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=1, max_length=255)
    phone_number: Optional[str] = None
    password: str = Field(..., min_length=6)
    site_id: Optional[str] = None
    role_id: Optional[str] = None

class ClientAccountUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    password: Optional[str] = None
    site_id: Optional[str] = None
    role_id: Optional[str] = None

class ClientAccountOut(BaseModel):
    client_id: str
    tenant_id: str
    name: str
    email: str
    phone_number: Optional[str] = None
    status: str
    created_at: datetime
    site_id: Optional[str] = None

    class Config:
        from_attributes = True

class ClientAccountDetailOut(ClientAccountOut):
    site_name: Optional[str] = None
    tenant_name: str
    tenant_code: str
    role_name: Optional[str] = None
    role_id: Optional[str] = None

class ClientLoginRequest(BaseModel):
    email: str
    password: str
    tenant_id: str

class ClientLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    client: ClientAccountOut


# ══════════════════════════════════════════════
# Task Role Schemas
# ══════════════════════════════════════════════

class TaskRoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    tenant_id: Optional[str] = None  # null = internal role
    permissions: List[str] = []

class TaskRoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None

class TaskRoleOut(BaseModel):
    role_id: str
    tenant_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    permissions: List[str] = []
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True

class TaskRoleAssignmentCreate(BaseModel):
    user_id: Optional[str] = None
    client_id: Optional[str] = None

class TaskRoleAssignmentOut(BaseModel):
    id: int
    task_role_id: str
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# Task Template Schemas
# ══════════════════════════════════════════════

class TaskItemCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    section_id: Optional[str] = None
    response_type: str = "right_wrong"
    requires_photo: bool = False
    alert_on_wrong: bool = True
    alert_on_note: bool = False
    sort_order: int = 0
    alert_role_ids: List[str] = []  # task_role_ids that receive alerts

class TaskItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    section_id: Optional[str] = None
    response_type: Optional[str] = None
    requires_photo: Optional[bool] = None
    alert_on_wrong: Optional[bool] = None
    alert_on_note: Optional[bool] = None
    sort_order: Optional[int] = None
    alert_role_ids: Optional[List[str]] = None

class TaskItemOut(BaseModel):
    item_id: str
    template_id: str
    section_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    response_type: str
    requires_photo: bool
    alert_on_wrong: bool
    alert_on_note: bool
    sort_order: int
    alert_role_ids: List[str] = []

    class Config:
        from_attributes = True

class TaskSectionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    sort_order: int = 0

class TaskSectionOut(BaseModel):
    section_id: str
    template_id: str
    title: str
    description: Optional[str] = None
    sort_order: int
    items: List[TaskItemOut] = []

    class Config:
        from_attributes = True

class TaskTemplateCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    site_id: Optional[str] = None
    is_recurring: bool = False
    recurrence_rule: Optional[str] = None
    deadline: Optional[datetime] = None
    sections: List[TaskSectionCreate] = []
    items: List[TaskItemCreate] = []  # Flat items (no section)

class TaskTemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    site_id: Optional[str] = None
    is_recurring: Optional[bool] = None
    recurrence_rule: Optional[str] = None
    deadline: Optional[datetime] = None
    is_active: Optional[bool] = None

class TaskTemplateOut(BaseModel):
    template_id: str
    title: str
    description: Optional[str] = None
    site_id: Optional[str] = None
    is_recurring: bool
    recurrence_rule: Optional[str] = None
    deadline: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    sections: List[TaskSectionOut] = []
    items: List[TaskItemOut] = []

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# Task Instance Schemas (Execution)
# ══════════════════════════════════════════════

class TaskInstanceAssign(BaseModel):
    template_id: str
    assigned_to: str  # user_id of leader
    site_id: Optional[str] = None
    due_date: Optional[datetime] = None

class TaskResponseSubmit(BaseModel):
    item_id: str
    result: Optional[str] = None  # pass / fail / na
    note: Optional[str] = None

class TaskInstanceSubmit(BaseModel):
    responses: List[TaskResponseSubmit]
    completed_offline: bool = False
    offline_completed_at: Optional[datetime] = None

class TaskResponseOut(BaseModel):
    response_id: str
    instance_id: str
    item_id: str
    result: Optional[str] = None
    note: Optional[str] = None
    photo_url: Optional[str] = None
    responded_at: datetime
    item_title: Optional[str] = None

    class Config:
        from_attributes = True

class TaskInstanceOut(BaseModel):
    instance_id: str
    template_id: str
    template_title: Optional[str] = None
    assigned_to: str
    assignee_name: Optional[str] = None
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    status: str
    due_date: Optional[datetime] = None
    completed_offline: bool = False
    review_status: Optional[str] = None
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    responses: List[TaskResponseOut] = []

    class Config:
        from_attributes = True

class TaskInstanceReview(BaseModel):
    review_status: str  # approved / rejected
    review_note: Optional[str] = None


# ══════════════════════════════════════════════
# Alert Schemas
# ══════════════════════════════════════════════

class TaskAlertDeliveryOut(BaseModel):
    delivery_id: str
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    recipient_name: Optional[str] = None
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TaskAlertOut(BaseModel):
    alert_id: str
    response_id: str
    instance_id: str
    item_id: str
    item_title: Optional[str] = None
    alert_type: str
    status: str
    escalation_minutes: Optional[int] = None
    escalated_at: Optional[datetime] = None
    created_at: datetime
    deliveries: List[TaskAlertDeliveryOut] = []

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# Comment Schemas
# ══════════════════════════════════════════════

class TaskInstanceCommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)

class TaskInstanceCommentOut(BaseModel):
    comment_id: str
    instance_id: str
    client_id: Optional[str] = None
    user_id: Optional[str] = None
    author_name: str = ""
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# Client Profile Response
# ══════════════════════════════════════════════

class ClientMeResponse(BaseModel):
    client_id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    name: str
    email: str
    phone_number: Optional[str] = None
    status: str
    permissions: List[str] = []
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════
# Task Instance Comments
# ══════════════════════════════════════════════

class TaskInstanceCommentCreate(BaseModel):
    content: str


class TaskInstanceCommentOut(BaseModel):
    comment_id: str
    instance_id: str
    client_id: Optional[str] = None
    user_id: Optional[str] = None
    author_name: Optional[str] = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
