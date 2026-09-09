"""
SecureTrack Platform — Task Inspection & Alert System Models

Multi-tenant task inspection system with:
- Tenants (client organisations like banks)
- Dynamic task roles & permissions (separate from UserRole)
- Hierarchical task templates (template → section → item)
- Task instances & responses
- Alert engine with per-item recipient routing
- Delivery tracking & escalation
"""
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Boolean, Text,
    ForeignKey, Index, JSON,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


# ══════════════════════════════════════════════
# Tenant (Client Organisation)
# ══════════════════════════════════════════════

class Tenant(Base):
    """
    Represents an external client organisation (e.g. a bank)
    that has limited, scoped access to the task system.
    """
    __tablename__ = "tenants"

    tenant_id = Column(String(36), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    tenant_code = Column(String(20), nullable=False, unique=True, index=True)  # Auto-generated login code
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    status = Column(String(30), nullable=False, default="pending_approval")
    logo_url = Column(String(500), nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), ForeignKey("users.user_id"), nullable=True)

    # Relationships
    site_accesses = relationship("TenantSiteAccess", back_populates="tenant", cascade="all, delete-orphan")
    task_roles = relationship("TaskRole", back_populates="tenant", cascade="all, delete-orphan")
    client_accounts = relationship("ClientAccount", back_populates="tenant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Tenant({self.tenant_id}, name={self.name})>"


class TenantSiteAccess(Base):
    """Maps which sites a tenant (client) can access."""
    __tablename__ = "tenant_site_access"
    __table_args__ = (
        Index('ix_tenant_site', 'tenant_id', 'site_id', unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", back_populates="site_accesses")
    site = relationship("Site")

    def __repr__(self):
        return f"<TenantSiteAccess(tenant={self.tenant_id}, site={self.site_id})>"


# ══════════════════════════════════════════════
# Client Accounts (Bank Users)
# ══════════════════════════════════════════════

class ClientAccount(Base):
    """
    A user account for a client (bank employee).
    Separate from the internal User model for security isolation.
    Authenticated via their own credentials, JWT contains tenant_id.
    """
    __tablename__ = "client_accounts"
    __table_args__ = (
        Index('ix_client_tenant_email', 'tenant_id', 'email', unique=True),
    )

    client_id = Column(String(36), primary_key=True, index=True)
    tenant_id = Column(String(36), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    phone_number = Column(String(20), nullable=True)
    password_hash = Column(String(255), nullable=False)
    status = Column(String(30), nullable=False, default="pending_approval")
    fcm_token = Column(String(500), nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), nullable=True)  # admin user_id or self-registered

    # Relationships
    tenant = relationship("Tenant", back_populates="client_accounts")
    role_assignments = relationship("TaskRoleAssignment", back_populates="client_account",
                                    cascade="all, delete-orphan",
                                    foreign_keys="TaskRoleAssignment.client_id")

    def __repr__(self):
        return f"<ClientAccount({self.client_id}, name={self.name}, tenant={self.tenant_id})>"


# ══════════════════════════════════════════════
# Task Roles & Permissions (Separate RBAC)
# ══════════════════════════════════════════════

class TaskRole(Base):
    """
    Dynamic roles for the task system.
    - tenant_id = NULL → internal role (our team)
    - tenant_id = <id> → client-scoped role (bank staff)
    """
    __tablename__ = "task_roles"

    role_id = Column(String(36), primary_key=True, index=True)
    tenant_id = Column(String(36), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)

    # JSON list of permission strings, e.g.:
    # ["tasks.view", "tasks.create", "tasks.approve", "alerts.receive", "reports.view"]
    permissions = Column(JSON, nullable=False, default=list)

    is_system = Column(Boolean, nullable=False, default=False)  # System-created, cannot delete

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="task_roles")
    assignments = relationship("TaskRoleAssignment", back_populates="role", cascade="all, delete-orphan")
    alert_recipients = relationship("TaskItemAlertRecipient", back_populates="role", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskRole({self.role_id}, name={self.name}, tenant={self.tenant_id})>"


class TaskRoleAssignment(Base):
    """
    Assigns a task role to either an internal user OR a client account.
    Exactly one of user_id / client_id must be set.
    """
    __tablename__ = "task_role_assignments"
    __table_args__ = (
        Index('ix_tra_user_role', 'user_id', 'task_role_id', unique=True),
        Index('ix_tra_client_role', 'client_id', 'task_role_id', unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_role_id = Column(String(36), ForeignKey("task_roles.role_id", ondelete="CASCADE"), nullable=False, index=True)

    # One of these is set
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    client_id = Column(String(36), ForeignKey("client_accounts.client_id", ondelete="CASCADE"), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    role = relationship("TaskRole", back_populates="assignments")
    user = relationship("User", foreign_keys=[user_id])
    client_account = relationship("ClientAccount", back_populates="role_assignments",
                                  foreign_keys=[client_id])

    def __repr__(self):
        target = self.user_id or self.client_id
        return f"<TaskRoleAssignment(role={self.task_role_id}, target={target})>"


# ══════════════════════════════════════════════
# Task Templates (Hierarchical: Template → Section → Item)
# ══════════════════════════════════════════════

class TaskTemplate(Base):
    """
    A reusable checklist template that can be assigned to leaders.
    Supports simple (flat items), grouped (sections→items), and nested structures.
    """
    __tablename__ = "task_templates"

    template_id = Column(String(36), primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    site_id = Column(String(36), ForeignKey("sites.site_id"), nullable=True, index=True)

    # Recurrence
    is_recurring = Column(Boolean, nullable=False, default=False)
    recurrence_rule = Column(String(30), nullable=True)  # daily, weekly, monthly, custom

    # Deadline for one-time tasks
    deadline = Column(DateTime, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), ForeignKey("users.user_id"), nullable=True)

    # Relationships
    site = relationship("Site")
    sections = relationship("TaskSection", back_populates="template",
                            cascade="all, delete-orphan", order_by="TaskSection.sort_order")
    items = relationship("TaskItem", back_populates="template",
                         cascade="all, delete-orphan", order_by="TaskItem.sort_order")
    instances = relationship("TaskInstance", back_populates="template", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskTemplate({self.template_id}, title={self.title})>"


class TaskSection(Base):
    """Optional grouping within a template (e.g. 'Safety', 'Equipment')."""
    __tablename__ = "task_sections"

    section_id = Column(String(36), primary_key=True, index=True)
    template_id = Column(String(36), ForeignKey("task_templates.template_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    template = relationship("TaskTemplate", back_populates="sections")
    items = relationship("TaskItem", back_populates="section",
                         cascade="all, delete-orphan", order_by="TaskItem.sort_order")

    def __repr__(self):
        return f"<TaskSection({self.section_id}, title={self.title})>"


class TaskItem(Base):
    """
    An individual check/task within a template.
    Can belong to a section (grouped) or directly to a template (flat).
    """
    __tablename__ = "task_items"
    __table_args__ = (
        Index('ix_task_item_template', 'template_id', 'sort_order'),
    )

    item_id = Column(String(36), primary_key=True, index=True)
    template_id = Column(String(36), ForeignKey("task_templates.template_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    section_id = Column(String(36), ForeignKey("task_sections.section_id", ondelete="SET NULL"),
                        nullable=True, index=True)

    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)

    # Response configuration
    response_type = Column(String(30), nullable=False, default="right_wrong")  # TaskItemResponseType
    requires_photo = Column(Boolean, nullable=False, default=False)

    # Alert configuration
    alert_on_wrong = Column(Boolean, nullable=False, default=True)
    alert_on_note = Column(Boolean, nullable=False, default=False)

    sort_order = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    template = relationship("TaskTemplate", back_populates="items")
    section = relationship("TaskSection", back_populates="items")
    alert_recipients = relationship("TaskItemAlertRecipient", back_populates="item",
                                    cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskItem({self.item_id}, title={self.title})>"


class TaskItemAlertRecipient(Base):
    """Which task roles receive alerts when this item fails."""
    __tablename__ = "task_item_alert_recipients"
    __table_args__ = (
        Index('ix_tiar_item_role', 'item_id', 'task_role_id', unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(36), ForeignKey("task_items.item_id", ondelete="CASCADE"), nullable=False, index=True)
    task_role_id = Column(String(36), ForeignKey("task_roles.role_id", ondelete="CASCADE"), nullable=False, index=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    item = relationship("TaskItem", back_populates="alert_recipients")
    role = relationship("TaskRole", back_populates="alert_recipients")

    def __repr__(self):
        return f"<TaskItemAlertRecipient(item={self.item_id}, role={self.task_role_id})>"


# ══════════════════════════════════════════════
# Task Instances & Responses (Execution Layer)
# ══════════════════════════════════════════════

class TaskInstance(Base):
    """
    A specific execution of a template — assigned to a leader, completed on-site.
    """
    __tablename__ = "task_instances"
    __table_args__ = (
        Index('ix_ti_assigned_status', 'assigned_to', 'status'),
        Index('ix_ti_site_status', 'site_id', 'status'),
    )

    instance_id = Column(String(36), primary_key=True, index=True)
    template_id = Column(String(36), ForeignKey("task_templates.template_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    assigned_to = Column(String(36), ForeignKey("users.user_id"), nullable=False, index=True)
    site_id = Column(String(36), ForeignKey("sites.site_id"), nullable=True, index=True)

    status = Column(String(30), nullable=False, default="pending")  # TaskInstanceStatus
    due_date = Column(DateTime, nullable=True)

    # Offline sync support
    completed_offline = Column(Boolean, nullable=False, default=False)
    offline_completed_at = Column(DateTime, nullable=True)

    # Review (client approve/reject)
    review_status = Column(String(30), nullable=True)  # approved / rejected
    reviewed_by = Column(String(36), nullable=True)     # client_id
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), nullable=True)

    # Relationships
    template = relationship("TaskTemplate", back_populates="instances")
    assignee = relationship("User", foreign_keys=[assigned_to])
    site = relationship("Site")
    responses = relationship("TaskResponse", back_populates="instance", cascade="all, delete-orphan")
    alerts = relationship("TaskAlert", back_populates="instance", cascade="all, delete-orphan")
    comments = relationship("TaskInstanceComment", back_populates="instance", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskInstance({self.instance_id}, template={self.template_id}, status={self.status})>"


class TaskResponse(Base):
    """Leader's answer for a single task item within an instance."""
    __tablename__ = "task_responses"
    __table_args__ = (
        Index('ix_tr_instance_item', 'instance_id', 'item_id', unique=True),
    )

    response_id = Column(String(36), primary_key=True, index=True)
    instance_id = Column(String(36), ForeignKey("task_instances.instance_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    item_id = Column(String(36), ForeignKey("task_items.item_id", ondelete="CASCADE"),
                     nullable=False, index=True)

    result = Column(String(10), nullable=True)  # pass / fail / na — TaskItemResult
    note = Column(Text, nullable=True)
    photo_url = Column(String(500), nullable=True)

    responded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    instance = relationship("TaskInstance", back_populates="responses")
    item = relationship("TaskItem")
    alerts = relationship("TaskAlert", back_populates="response", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskResponse({self.response_id}, result={self.result})>"


# ══════════════════════════════════════════════
# Alert Engine (Triggered by failed items)
# ══════════════════════════════════════════════

class TaskAlert(Base):
    """
    An alert record created when a leader marks an item as 'wrong'
    or writes a note on an alertable item.
    """
    __tablename__ = "task_alerts"
    __table_args__ = (
        Index('ix_ta_status', 'status'),
    )

    alert_id = Column(String(36), primary_key=True, index=True)
    response_id = Column(String(36), ForeignKey("task_responses.response_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    instance_id = Column(String(36), ForeignKey("task_instances.instance_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    item_id = Column(String(36), ForeignKey("task_items.item_id", ondelete="CASCADE"),
                     nullable=False, index=True)

    alert_type = Column(String(20), nullable=False)  # TaskAlertType: wrong / note
    status = Column(String(30), nullable=False, default="pending")  # TaskAlertStatus

    # Escalation tracking
    escalation_minutes = Column(Integer, nullable=True)  # Auto-escalate after N minutes
    escalated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    response = relationship("TaskResponse", back_populates="alerts")
    instance = relationship("TaskInstance", back_populates="alerts")
    item = relationship("TaskItem")
    deliveries = relationship("TaskAlertDelivery", back_populates="alert", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<TaskAlert({self.alert_id}, type={self.alert_type}, status={self.status})>"


class TaskAlertDelivery(Base):
    """
    Per-recipient delivery tracking for an alert.
    Tracks whether each user has received and acknowledged the alarm.
    """
    __tablename__ = "task_alert_deliveries"
    __table_args__ = (
        Index('ix_tad_alert_user', 'alert_id', 'user_id'),
        Index('ix_tad_alert_client', 'alert_id', 'client_id'),
    )

    delivery_id = Column(String(36), primary_key=True, index=True)
    alert_id = Column(String(36), ForeignKey("task_alerts.alert_id", ondelete="CASCADE"),
                      nullable=False, index=True)

    # One of these is set (internal user or client account)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True)
    client_id = Column(String(36), ForeignKey("client_accounts.client_id", ondelete="CASCADE"), nullable=True)

    delivered_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    alert = relationship("TaskAlert", back_populates="deliveries")

    def __repr__(self):
        target = self.user_id or self.client_id
        return f"<TaskAlertDelivery(alert={self.alert_id}, target={target})>"


# ══════════════════════════════════════════════
# Task Instance Comments
# ══════════════════════════════════════════════

class TaskInstanceComment(Base):
    """
    A comment/note on a task instance — can be from a client account or internal user.
    """
    __tablename__ = "task_instance_comments"
    __table_args__ = (
        Index('ix_tic_instance', 'instance_id'),
    )

    comment_id = Column(String(36), primary_key=True, index=True)
    instance_id = Column(String(36), ForeignKey("task_instances.instance_id", ondelete="CASCADE"),
                         nullable=False, index=True)

    # One of these is set (internal user or client account)
    client_id = Column(String(36), ForeignKey("client_accounts.client_id", ondelete="CASCADE"), nullable=True)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True)

    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    instance = relationship("TaskInstance", back_populates="comments")

    def __repr__(self):
        author = self.client_id or self.user_id
        return f"<TaskInstanceComment({self.comment_id}, author={author})>"
