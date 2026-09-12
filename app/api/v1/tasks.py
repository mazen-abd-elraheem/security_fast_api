"""
SecureTrack Platform — Task Inspection System API
Admin + Leader endpoints for template CRUD, instance assignment, and response submission.
"""
import uuid
import logging
import random
import string
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.api.deps import require_role, get_current_user
from app.enums import UserRole, TaskInstanceStatus, TaskAlertType, TaskAlertStatus
from app.models.user import User
from app.models.task_models import (
    Tenant, TenantSiteAccess, ClientAccount,
    TaskRole, TaskRoleAssignment,
    TaskTemplate, TaskSection, TaskItem, TaskItemAlertRecipient,
    TaskInstance, TaskResponse,
    TaskAlert, TaskAlertDelivery,
)
from app.schemas.task_schemas import (
    TenantCreate, TenantUpdate, TenantOut, TenantSiteAccessCreate,
    ClientAccountCreate, ClientAccountUpdate, ClientAccountOut, ClientAccountDetailOut,
    TaskRoleCreate, TaskRoleUpdate, TaskRoleOut, TaskRoleAssignmentCreate, TaskRoleAssignmentOut,
    TaskTemplateCreate, TaskTemplateUpdate, TaskTemplateOut,
    TaskSectionCreate, TaskSectionOut,
    TaskItemCreate, TaskItemUpdate, TaskItemOut,
    TaskInstanceAssign, TaskInstanceSubmit, TaskInstanceOut, TaskInstanceReview,
    TaskResponseOut,
    TaskAlertOut,
)
from app.core.security import hash_password

logger = logging.getLogger(__name__)
router = APIRouter()


# ══════════════════════════════════════════════
# TENANT MANAGEMENT (Admin only)
# ══════════════════════════════════════════════

@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(
    body: TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a new client tenant (e.g. a bank)."""
    # Auto-generate a unique tenant code (T-XXXXXX)
    while True:
        code = 'T-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not db.query(Tenant).filter(Tenant.tenant_code == code).first():
            break
    tenant = Tenant(
        tenant_id=str(uuid.uuid4()),
        name=body.name,
        tenant_code=code,
        contact_email=body.contact_email,
        contact_phone=body.contact_phone,
        status="active",
        created_by=current_user.user_id,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    logger.info(f"Tenant created: {tenant.name} (code={code}) by {current_user.user_id}")
    return tenant


@router.get("/tenants", response_model=List[TenantOut])
def list_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all tenants."""
    return db.query(Tenant).order_by(Tenant.created_at.desc()).all()


@router.get("/tenants/lookup")
def lookup_tenant_by_code(
    code: str = Query(..., min_length=1, description="Tenant code (e.g. T-AB12CD)"),
    db: Session = Depends(get_db),
):
    """Public endpoint — resolve a tenant code to tenant_id + name for client login."""
    tenant = db.query(Tenant).filter(
        Tenant.tenant_code == code.upper().strip(),
        Tenant.status == "active",
    ).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Invalid tenant code")
    return {"tenant_id": tenant.tenant_id, "name": tenant.name}


@router.put("/tenants/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update tenant details or status (activate/deactivate)."""
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    for field, val in body.dict(exclude_unset=True).items():
        setattr(tenant, field, val)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post("/tenants/{tenant_id}/sites", status_code=201)
def assign_site_to_tenant(
    tenant_id: str,
    body: TenantSiteAccessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Grant a tenant access to a specific site."""
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    exists = db.query(TenantSiteAccess).filter_by(
        tenant_id=tenant_id, site_id=body.site_id
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Site already assigned to this tenant")

    access = TenantSiteAccess(
        tenant_id=tenant_id,
        site_id=body.site_id,
    )
    db.add(access)
    db.commit()
    return {"message": "Site assigned to tenant"}


@router.delete("/tenants/{tenant_id}/sites/{site_id}", status_code=200)
def remove_site_from_tenant(
    tenant_id: str,
    site_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Remove a site from a tenant's access."""
    access = db.query(TenantSiteAccess).filter_by(
        tenant_id=tenant_id, site_id=site_id
    ).first()
    if not access:
        raise HTTPException(status_code=404, detail="Site access not found")
    db.delete(access)
    db.commit()
    return {"message": "Site removed from tenant"}


@router.get("/tenants/{tenant_id}/sites")
def list_tenant_sites(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List sites assigned to a tenant."""
    accesses = db.query(TenantSiteAccess).filter_by(tenant_id=tenant_id).all()
    from app.models.site import Site
    site_ids = [a.site_id for a in accesses]
    sites = db.query(Site).filter(Site.site_id.in_(site_ids)).all() if site_ids else []
    return [{"site_id": s.site_id, "name": s.name, "region": s.region} for s in sites]


# ══════════════════════════════════════════════
# CLIENT ACCOUNT MANAGEMENT (Admin only)
# ══════════════════════════════════════════════

@router.post("/tenants/{tenant_id}/clients", response_model=ClientAccountOut, status_code=201)
def create_client_account(
    tenant_id: str,
    body: ClientAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin creates a client user account for a tenant."""
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    exists = db.query(ClientAccount).filter_by(
        tenant_id=tenant_id, email=body.email
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered for this tenant")

    client = ClientAccount(
        client_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=body.name,
        email=body.email,
        phone_number=body.phone_number,
        password_hash=hash_password(body.password),
        status="active",
        created_by=current_user.user_id,
        site_id=body.site_id,
    )
    db.add(client)
    
    if body.role_id:
        role = db.query(TaskRole).filter_by(role_id=body.role_id).first()
        if role:
            assignment = TaskRoleAssignment(
                task_role_id=body.role_id,
                client_id=client.client_id
            )
            db.add(assignment)

    db.commit()
    db.refresh(client)
    return client


@router.get("/tenants/{tenant_id}/clients", response_model=List[ClientAccountOut])
def list_client_accounts(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all client accounts for a tenant."""
    return db.query(ClientAccount).filter_by(tenant_id=tenant_id).order_by(ClientAccount.created_at.desc()).all()


@router.get("/tenants/{tenant_id}/clients/detailed", response_model=List[ClientAccountDetailOut])
def list_client_accounts_detailed(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all client accounts for a tenant with detailed relations (Site, Role)."""
    tenant = db.query(Tenant).filter_by(tenant_id=tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    clients = db.query(ClientAccount).filter_by(tenant_id=tenant_id).order_by(ClientAccount.created_at.desc()).all()
    results = []
    
    for client in clients:
        site_name = None
        if client.site_id:
            from app.models.site import Site
            site = db.query(Site).filter_by(site_id=client.site_id).first()
            if site:
                site_name = site.name
                
        role_name = None
        role_id = None
        assignment = db.query(TaskRoleAssignment).filter_by(client_id=client.client_id).first()
        if assignment:
            role = db.query(TaskRole).filter_by(role_id=assignment.task_role_id).first()
            if role:
                role_name = role.name
                role_id = role.role_id
                
        results.append(ClientAccountDetailOut(
            client_id=client.client_id,
            tenant_id=client.tenant_id,
            name=client.name,
            email=client.email,
            phone_number=client.phone_number,
            status=client.status,
            created_at=client.created_at,
            site_id=client.site_id,
            site_name=site_name,
            tenant_name=tenant.name,
            tenant_code=tenant.tenant_code,
            role_name=role_name,
            role_id=role_id,
        ))
        
    return results


@router.put("/clients/{client_id}/status")
def toggle_client_status(
    client_id: str,
    status_val: str = Query(..., alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Activate or deactivate a client account."""
    client = db.query(ClientAccount).filter_by(client_id=client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client account not found")
    client.status = status_val
    db.commit()
    return {"message": f"Client account {status_val}"}

@router.put("/clients/{client_id}", response_model=ClientAccountOut)
def update_client_account(
    client_id: str,
    update_data: ClientAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin updates a client user account."""
    client = db.query(ClientAccount).filter_by(client_id=client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client account not found")

    if update_data.name is not None:
        client.name = update_data.name
    if update_data.email is not None:
        # Check email conflict
        exists = db.query(ClientAccount).filter(
            ClientAccount.email == update_data.email,
            ClientAccount.client_id != client_id
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Email already used")
        client.email = update_data.email
    if update_data.phone_number is not None:
        client.phone_number = update_data.phone_number
    if update_data.password is not None and len(update_data.password) > 0:
        client.password_hash = hash_password(update_data.password)
    if update_data.site_id is not None:
        # If passed as empty string, treat as removing site
        client.site_id = update_data.site_id if update_data.site_id else None
        
    if update_data.role_id is not None:
        # Remove existing assignment
        db.query(TaskRoleAssignment).filter_by(client_id=client_id).delete()
        if update_data.role_id:
            role = db.query(TaskRole).filter_by(role_id=update_data.role_id).first()
            if role:
                assignment = TaskRoleAssignment(
                    task_role_id=update_data.role_id,
                    client_id=client.client_id
                )
                db.add(assignment)

    db.commit()
    db.refresh(client)
    return client

# ══════════════════════════════════════════════
# TASK ROLE MANAGEMENT (Admin only)
# ══════════════════════════════════════════════

@router.post("/roles", response_model=TaskRoleOut, status_code=201)
def create_task_role(
    body: TaskRoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a task role (internal or client-scoped)."""
    role = TaskRole(
        role_id=str(uuid.uuid4()),
        tenant_id=body.tenant_id,
        name=body.name,
        description=body.description,
        permissions=body.permissions,
        created_by=current_user.user_id,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.get("/roles", response_model=List[TaskRoleOut])
def list_task_roles(
    tenant_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List task roles. Filter by tenant_id, or omit for internal roles."""
    q = db.query(TaskRole)
    if tenant_id:
        q = q.filter(TaskRole.tenant_id == tenant_id)
    else:
        q = q.filter(TaskRole.tenant_id.is_(None))
    return q.order_by(TaskRole.created_at.desc()).all()


@router.put("/roles/{role_id}", response_model=TaskRoleOut)
def update_task_role(
    role_id: str,
    body: TaskRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update a task role's name, description, or permissions."""
    role = db.query(TaskRole).filter(TaskRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Task role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="Cannot modify system roles")
    for field, val in body.dict(exclude_unset=True).items():
        setattr(role, field, val)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}", status_code=200)
def delete_task_role(
    role_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete a task role (non-system only)."""
    role = db.query(TaskRole).filter(TaskRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Task role not found")
    if role.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete system roles")
    db.delete(role)
    db.commit()
    return {"message": "Task role deleted"}


@router.post("/roles/{role_id}/assign", status_code=201)
def assign_task_role(
    role_id: str,
    body: TaskRoleAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Assign a task role to a user or client account."""
    role = db.query(TaskRole).filter(TaskRole.role_id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Task role not found")

    if not body.user_id and not body.client_id:
        raise HTTPException(status_code=400, detail="Must specify user_id or client_id")

    # Check for existing assignment
    q = db.query(TaskRoleAssignment).filter_by(task_role_id=role_id)
    if body.user_id:
        q = q.filter_by(user_id=body.user_id)
    else:
        q = q.filter_by(client_id=body.client_id)
    if q.first():
        raise HTTPException(status_code=409, detail="Role already assigned")

    assignment = TaskRoleAssignment(
        task_role_id=role_id,
        user_id=body.user_id,
        client_id=body.client_id,
    )
    db.add(assignment)
    db.commit()
    return {"message": "Task role assigned"}


@router.get("/roles/{role_id}/assignments", response_model=List[TaskRoleAssignmentOut])
def get_task_role_assignments(
    role_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get all assignments for a task role."""
    assignments = db.query(TaskRoleAssignment).filter_by(task_role_id=role_id).all()
    results = []
    for a in assignments:
        name, email = None, None
        if a.user_id:
            from app.models.user import User as UserModel
            u = db.query(UserModel).filter_by(user_id=a.user_id).first()
            if u:
                name, email = u.name, u.email
        elif a.client_id:
            c = db.query(ClientAccount).filter_by(client_id=a.client_id).first()
            if c:
                name, email = c.name, c.email
        
        results.append(TaskRoleAssignmentOut(
            id=a.id,
            task_role_id=a.task_role_id,
            user_id=a.user_id,
            client_id=a.client_id,
            user_name=name,
            user_email=email
        ))
    return results


@router.delete("/roles/{role_id}/unassign", status_code=200)
def unassign_task_role(
    role_id: str,
    user_id: Optional[str] = None,
    client_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Remove a task role assignment."""
    q = db.query(TaskRoleAssignment).filter_by(task_role_id=role_id)
    if user_id:
        q = q.filter_by(user_id=user_id)
    elif client_id:
        q = q.filter_by(client_id=client_id)
    else:
        raise HTTPException(status_code=400, detail="Must specify user_id or client_id")

    assignment = q.first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.delete(assignment)
    db.commit()
    return {"message": "Task role unassigned"}


# ══════════════════════════════════════════════
# TASK TEMPLATE CRUD (Admin only)
# ══════════════════════════════════════════════

@router.post("/templates", response_model=TaskTemplateOut, status_code=201)
def create_template(
    body: TaskTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a task template with optional sections and items."""
    template = TaskTemplate(
        template_id=str(uuid.uuid4()),
        title=body.title,
        description=body.description,
        site_id=body.site_id,
        is_recurring=body.is_recurring,
        recurrence_rule=body.recurrence_rule,
        deadline=body.deadline,
        created_by=current_user.user_id,
    )
    db.add(template)
    db.flush()

    # Create sections
    section_map = {}  # local index → section_id for item assignment
    for idx, sec_data in enumerate(body.sections):
        section = TaskSection(
            section_id=str(uuid.uuid4()),
            template_id=template.template_id,
            title=sec_data.title,
            description=sec_data.description,
            sort_order=sec_data.sort_order or idx,
        )
        db.add(section)
        db.flush()
        section_map[idx] = section.section_id

    # Create flat items (no section)
    for idx, item_data in enumerate(body.items):
        item = TaskItem(
            item_id=str(uuid.uuid4()),
            template_id=template.template_id,
            section_id=item_data.section_id,
            title=item_data.title,
            description=item_data.description,
            response_type=item_data.response_type,
            requires_photo=item_data.requires_photo,
            alert_on_wrong=item_data.alert_on_wrong,
            alert_on_note=item_data.alert_on_note,
            sort_order=item_data.sort_order or idx,
        )
        db.add(item)
        db.flush()

        # Set alert recipients
        for role_id in item_data.alert_role_ids:
            recipient = TaskItemAlertRecipient(
                item_id=item.item_id,
                task_role_id=role_id,
            )
            db.add(recipient)

    db.commit()
    return _load_template_full(db, template.template_id)


@router.get("/templates", response_model=List[TaskTemplateOut])
def list_templates(
    site_id: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.LEADER)),
):
    """List task templates with optional site filter."""
    q = db.query(TaskTemplate)
    if active_only:
        q = q.filter(TaskTemplate.is_active == True)
    if site_id:
        q = q.filter(TaskTemplate.site_id == site_id)
    templates = q.order_by(TaskTemplate.created_at.desc()).all()
    return [_load_template_full(db, t.template_id) for t in templates]


@router.get("/templates/{template_id}", response_model=TaskTemplateOut)
def get_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.LEADER)),
):
    """Get a single template with all sections and items."""
    result = _load_template_full(db, template_id)
    if not result:
        raise HTTPException(status_code=404, detail="Template not found")
    return result


@router.put("/templates/{template_id}", response_model=TaskTemplateOut)
def update_template(
    template_id: str,
    body: TaskTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update template metadata."""
    template = db.query(TaskTemplate).filter_by(template_id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    for field, val in body.dict(exclude_unset=True).items():
        setattr(template, field, val)
    db.commit()
    return _load_template_full(db, template_id)


@router.delete("/templates/{template_id}", status_code=200)
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete a template and all its sections/items."""
    template = db.query(TaskTemplate).filter_by(template_id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(template)
    db.commit()
    return {"message": "Template deleted"}


# ── Sections ──

@router.post("/templates/{template_id}/sections", response_model=TaskSectionOut, status_code=201)
def add_section(
    template_id: str,
    body: TaskSectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Add a section to a template."""
    template = db.query(TaskTemplate).filter_by(template_id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    section = TaskSection(
        section_id=str(uuid.uuid4()),
        template_id=template_id,
        title=body.title,
        description=body.description,
        sort_order=body.sort_order,
    )
    db.add(section)
    db.commit()
    db.refresh(section)
    return TaskSectionOut(
        section_id=section.section_id,
        template_id=section.template_id,
        title=section.title,
        description=section.description,
        sort_order=section.sort_order,
        items=[],
    )


# ── Items ──

@router.post("/templates/{template_id}/items", response_model=TaskItemOut, status_code=201)
def add_item(
    template_id: str,
    body: TaskItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Add an item to a template (optionally within a section)."""
    template = db.query(TaskTemplate).filter_by(template_id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    item = TaskItem(
        item_id=str(uuid.uuid4()),
        template_id=template_id,
        section_id=body.section_id,
        title=body.title,
        description=body.description,
        response_type=body.response_type,
        requires_photo=body.requires_photo,
        alert_on_wrong=body.alert_on_wrong,
        alert_on_note=body.alert_on_note,
        sort_order=body.sort_order,
    )
    db.add(item)
    db.flush()

    for role_id in body.alert_role_ids:
        db.add(TaskItemAlertRecipient(item_id=item.item_id, task_role_id=role_id))

    db.commit()
    db.refresh(item)
    return TaskItemOut(
        item_id=item.item_id,
        template_id=item.template_id,
        section_id=item.section_id,
        title=item.title,
        description=item.description,
        response_type=item.response_type,
        requires_photo=item.requires_photo,
        alert_on_wrong=item.alert_on_wrong,
        alert_on_note=item.alert_on_note,
        sort_order=item.sort_order,
        alert_role_ids=body.alert_role_ids,
    )


@router.put("/items/{item_id}", response_model=TaskItemOut)
def update_item(
    item_id: str,
    body: TaskItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update a task item."""
    item = db.query(TaskItem).filter_by(item_id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    update_data = body.dict(exclude_unset=True)
    alert_role_ids = update_data.pop("alert_role_ids", None)

    for field, val in update_data.items():
        setattr(item, field, val)

    if alert_role_ids is not None:
        # Replace alert recipients
        db.query(TaskItemAlertRecipient).filter_by(item_id=item_id).delete()
        for role_id in alert_role_ids:
            db.add(TaskItemAlertRecipient(item_id=item_id, task_role_id=role_id))

    db.commit()
    db.refresh(item)
    recipients = db.query(TaskItemAlertRecipient).filter_by(item_id=item_id).all()
    return TaskItemOut(
        item_id=item.item_id,
        template_id=item.template_id,
        section_id=item.section_id,
        title=item.title,
        description=item.description,
        response_type=item.response_type,
        requires_photo=item.requires_photo,
        alert_on_wrong=item.alert_on_wrong,
        alert_on_note=item.alert_on_note,
        sort_order=item.sort_order,
        alert_role_ids=[r.task_role_id for r in recipients],
    )


@router.delete("/items/{item_id}", status_code=200)
def delete_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete a task item."""
    item = db.query(TaskItem).filter_by(item_id=item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"message": "Item deleted"}


# ══════════════════════════════════════════════
# TASK INSTANCE MANAGEMENT (Admin assigns, Leader completes)
# ══════════════════════════════════════════════

@router.post("/assign", response_model=TaskInstanceOut, status_code=201)
def assign_task(
    body: TaskInstanceAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Assign a template to a leader, creating a task instance."""
    template = db.query(TaskTemplate).filter_by(template_id=body.template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    instance = TaskInstance(
        instance_id=str(uuid.uuid4()),
        template_id=body.template_id,
        assigned_to=body.assigned_to,
        site_id=body.site_id or template.site_id,
        status=TaskInstanceStatus.PENDING,
        due_date=body.due_date or template.deadline,
        created_by=current_user.user_id,
    )
    db.add(instance)
    db.commit()
    return _load_instance_full(db, instance.instance_id)


@router.get("/instances", response_model=List[TaskInstanceOut])
def list_instances(
    status_filter: Optional[str] = None,
    site_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all task instances with filters."""
    q = db.query(TaskInstance)
    if status_filter:
        q = q.filter(TaskInstance.status == status_filter)
    if site_id:
        q = q.filter(TaskInstance.site_id == site_id)
    if assigned_to:
        q = q.filter(TaskInstance.assigned_to == assigned_to)
    instances = q.order_by(TaskInstance.created_at.desc()).all()
    return [_load_instance_full(db, i.instance_id) for i in instances]


@router.get("/instances/{instance_id}", response_model=TaskInstanceOut)
def get_instance(
    instance_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single task instance with responses."""
    result = _load_instance_full(db, instance_id)
    if not result:
        raise HTTPException(status_code=404, detail="Instance not found")
    return result


# ── Leader Endpoints ──

@router.get("/my-tasks", response_model=List[TaskInstanceOut])
def my_tasks(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEADER)),
):
    """Get task instances assigned to the current leader."""
    q = db.query(TaskInstance).filter(TaskInstance.assigned_to == current_user.user_id)
    if status_filter:
        q = q.filter(TaskInstance.status == status_filter)
    instances = q.order_by(TaskInstance.created_at.desc()).all()
    return [_load_instance_full(db, i.instance_id) for i in instances]


@router.post("/instances/{instance_id}/respond", response_model=TaskInstanceOut)
def submit_responses(
    instance_id: str,
    body: TaskInstanceSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEADER)),
):
    """Leader submits all responses for a task instance."""
    instance = db.query(TaskInstance).filter_by(instance_id=instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    if instance.assigned_to != current_user.user_id:
        raise HTTPException(status_code=403, detail="Not assigned to you")
    if instance.status in (TaskInstanceStatus.COMPLETED, TaskInstanceStatus.REVIEWED):
        raise HTTPException(status_code=400, detail="Already completed")

    instance.status = TaskInstanceStatus.IN_PROGRESS

    alerts_to_create = []

    for resp_data in body.responses:
        # Check for existing response (idempotent for offline sync)
        existing = db.query(TaskResponse).filter_by(
            instance_id=instance_id, item_id=resp_data.item_id
        ).first()

        if existing:
            existing.result = resp_data.result
            existing.note = resp_data.note
            existing.responded_at = datetime.now(timezone.utc)
            response = existing
        else:
            response = TaskResponse(
                response_id=str(uuid.uuid4()),
                instance_id=instance_id,
                item_id=resp_data.item_id,
                result=resp_data.result,
                note=resp_data.note,
                responded_at=datetime.now(timezone.utc),
            )
            db.add(response)
            db.flush()

        # Check if this response should trigger an alert
        item = db.query(TaskItem).filter_by(item_id=resp_data.item_id).first()
        if item:
            should_alert = False
            alert_type = None

            if resp_data.result == "fail" and item.alert_on_wrong:
                should_alert = True
                alert_type = TaskAlertType.WRONG
            elif resp_data.note and item.alert_on_note:
                should_alert = True
                alert_type = TaskAlertType.NOTE

            if should_alert:
                alert = TaskAlert(
                    alert_id=str(uuid.uuid4()),
                    response_id=response.response_id,
                    instance_id=instance_id,
                    item_id=resp_data.item_id,
                    alert_type=alert_type,
                    status=TaskAlertStatus.PENDING,
                    escalation_minutes=15,  # Auto-escalate after 15 min
                )
                db.add(alert)
                db.flush()
                alerts_to_create.append(alert)

    # Mark as completed
    instance.status = TaskInstanceStatus.COMPLETED
    instance.completed_at = datetime.now(timezone.utc)
    instance.completed_offline = body.completed_offline
    instance.offline_completed_at = body.offline_completed_at

    db.commit()

    # Create alert deliveries (after commit so IDs are stable)
    for alert in alerts_to_create:
        _create_alert_deliveries(db, alert)

    db.commit()

    # Dispatch notifications (DB + FCM push) for each alert
    from app.core.alert_engine import dispatch_alert_notifications
    for alert in alerts_to_create:
        dispatch_alert_notifications(db, alert)

    logger.info(f"Task instance {instance_id} completed by {current_user.user_id}, "
                f"{len(alerts_to_create)} alerts created")

    return _load_instance_full(db, instance_id)


@router.post("/responses/{response_id}/photo")
async def upload_response_photo(
    response_id: str,
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.LEADER)),
):
    """Upload photo evidence for a task response."""
    response = db.query(TaskResponse).filter_by(response_id=response_id).first()
    if not response:
        raise HTTPException(status_code=404, detail="Response not found")

    import os
    from app.core.config import get_settings
    settings = get_settings()

    upload_dir = os.path.join(settings.UPLOAD_DIR, "task_photos")
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(photo.filename)[1] if photo.filename else ".jpg"
    filename = f"{response_id}{ext}"
    filepath = os.path.join(upload_dir, filename)

    content = await photo.read()
    with open(filepath, "wb") as f:
        f.write(content)

    response.photo_url = f"/static/uploads/task_photos/{filename}"
    db.commit()

    return {"photo_url": response.photo_url}


# ── Alert Endpoints ──

@router.get("/alerts", response_model=List[TaskAlertOut])
def list_alerts(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all task alerts."""
    q = db.query(TaskAlert)
    if status_filter:
        q = q.filter(TaskAlert.status == status_filter)
    alerts = q.order_by(TaskAlert.created_at.desc()).all()
    return [_load_alert_full(db, a) for a in alerts]


@router.get("/alerts/pending")
def my_pending_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get unacknowledged alerts for the current user."""
    deliveries = db.query(TaskAlertDelivery).filter(
        TaskAlertDelivery.user_id == current_user.user_id,
        TaskAlertDelivery.acknowledged_at.is_(None),
    ).all()

    results = []
    for d in deliveries:
        alert = db.query(TaskAlert).filter_by(alert_id=d.alert_id).first()
        if alert:
            item = db.query(TaskItem).filter_by(item_id=alert.item_id).first()
            results.append({
                "delivery_id": d.delivery_id,
                "alert_id": alert.alert_id,
                "alert_type": alert.alert_type,
                "item_title": item.title if item else None,
                "instance_id": alert.instance_id,
                "created_at": alert.created_at.isoformat(),
            })
    return results


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Acknowledge an alert (stops the alarm)."""
    delivery = db.query(TaskAlertDelivery).filter(
        TaskAlertDelivery.alert_id == alert_id,
        TaskAlertDelivery.user_id == current_user.user_id,
    ).first()

    if not delivery:
        raise HTTPException(status_code=404, detail="Alert delivery not found for this user")

    delivery.acknowledged_at = datetime.now(timezone.utc)

    # Check if all deliveries are acknowledged → mark alert as acknowledged
    alert = db.query(TaskAlert).filter_by(alert_id=alert_id).first()
    if alert:
        unacked = db.query(TaskAlertDelivery).filter(
            TaskAlertDelivery.alert_id == alert_id,
            TaskAlertDelivery.acknowledged_at.is_(None),
        ).count()
        if unacked <= 1:  # This is the last one
            alert.status = TaskAlertStatus.ACKNOWLEDGED

    db.commit()
    return {"message": "Alert acknowledged"}


# ══════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════

def _load_template_full(db: Session, template_id: str) -> Optional[TaskTemplateOut]:
    """Load a template with all sections, items, and alert recipients."""
    template = db.query(TaskTemplate).filter_by(template_id=template_id).first()
    if not template:
        return None

    sections_out = []
    for section in template.sections:
        items_out = []
        for item in section.items:
            recipients = db.query(TaskItemAlertRecipient).filter_by(item_id=item.item_id).all()
            items_out.append(TaskItemOut(
                item_id=item.item_id,
                template_id=item.template_id,
                section_id=item.section_id,
                title=item.title,
                description=item.description,
                response_type=item.response_type,
                requires_photo=item.requires_photo,
                alert_on_wrong=item.alert_on_wrong,
                alert_on_note=item.alert_on_note,
                sort_order=item.sort_order,
                alert_role_ids=[r.task_role_id for r in recipients],
            ))
        sections_out.append(TaskSectionOut(
            section_id=section.section_id,
            template_id=section.template_id,
            title=section.title,
            description=section.description,
            sort_order=section.sort_order,
            items=items_out,
        ))

    # Flat items (no section)
    flat_items = db.query(TaskItem).filter_by(
        template_id=template_id, section_id=None
    ).order_by(TaskItem.sort_order).all()
    flat_items_out = []
    for item in flat_items:
        recipients = db.query(TaskItemAlertRecipient).filter_by(item_id=item.item_id).all()
        flat_items_out.append(TaskItemOut(
            item_id=item.item_id,
            template_id=item.template_id,
            section_id=None,
            title=item.title,
            description=item.description,
            response_type=item.response_type,
            requires_photo=item.requires_photo,
            alert_on_wrong=item.alert_on_wrong,
            alert_on_note=item.alert_on_note,
            sort_order=item.sort_order,
            alert_role_ids=[r.task_role_id for r in recipients],
        ))

    return TaskTemplateOut(
        template_id=template.template_id,
        title=template.title,
        description=template.description,
        site_id=template.site_id,
        is_recurring=template.is_recurring,
        recurrence_rule=template.recurrence_rule,
        deadline=template.deadline,
        is_active=template.is_active,
        created_at=template.created_at,
        sections=sections_out,
        items=flat_items_out,
    )


def _load_instance_full(db: Session, instance_id: str) -> Optional[TaskInstanceOut]:
    """Load an instance with assignee, site, and responses."""
    instance = db.query(TaskInstance).filter_by(instance_id=instance_id).first()
    if not instance:
        return None

    assignee = db.query(User).filter_by(user_id=instance.assigned_to).first()
    template = db.query(TaskTemplate).filter_by(template_id=instance.template_id).first()

    from app.models.site import Site
    site = db.query(Site).filter_by(site_id=instance.site_id).first() if instance.site_id else None

    responses = db.query(TaskResponse).filter_by(instance_id=instance_id).all()
    responses_out = []
    for r in responses:
        item = db.query(TaskItem).filter_by(item_id=r.item_id).first()
        responses_out.append(TaskResponseOut(
            response_id=r.response_id,
            instance_id=r.instance_id,
            item_id=r.item_id,
            result=r.result,
            note=r.note,
            photo_url=r.photo_url,
            responded_at=r.responded_at,
            item_title=item.title if item else None,
        ))

    return TaskInstanceOut(
        instance_id=instance.instance_id,
        template_id=instance.template_id,
        template_title=template.title if template else None,
        assigned_to=instance.assigned_to,
        assignee_name=assignee.name if assignee else None,
        site_id=instance.site_id,
        site_name=site.name if site else None,
        status=instance.status,
        due_date=instance.due_date,
        completed_offline=instance.completed_offline,
        review_status=instance.review_status,
        reviewed_by=instance.reviewed_by,
        review_note=instance.review_note,
        reviewed_at=instance.reviewed_at,
        completed_at=instance.completed_at,
        created_at=instance.created_at,
        responses=responses_out,
    )


def _load_alert_full(db: Session, alert: TaskAlert) -> TaskAlertOut:
    """Enrich an alert with item title and delivery details."""
    item = db.query(TaskItem).filter_by(item_id=alert.item_id).first()
    deliveries = db.query(TaskAlertDelivery).filter_by(alert_id=alert.alert_id).all()

    deliveries_out = []
    for d in deliveries:
        name = None
        if d.user_id:
            user = db.query(User).filter_by(user_id=d.user_id).first()
            name = user.name if user else None
        elif d.client_id:
            client = db.query(ClientAccount).filter_by(client_id=d.client_id).first()
            name = client.name if client else None
        deliveries_out.append(TaskAlertDeliveryOut(
            delivery_id=d.delivery_id,
            user_id=d.user_id,
            client_id=d.client_id,
            recipient_name=name,
            delivered_at=d.delivered_at,
            acknowledged_at=d.acknowledged_at,
        ))

    return TaskAlertOut(
        alert_id=alert.alert_id,
        response_id=alert.response_id,
        instance_id=alert.instance_id,
        item_id=alert.item_id,
        item_title=item.title if item else None,
        alert_type=alert.alert_type,
        status=alert.status,
        escalation_minutes=alert.escalation_minutes,
        escalated_at=alert.escalated_at,
        created_at=alert.created_at,
        deliveries=deliveries_out,
    )


def _create_alert_deliveries(db: Session, alert: TaskAlert):
    """
    Resolve alert recipients from TaskItemAlertRecipient → TaskRoleAssignment
    and create TaskAlertDelivery records for each target user/client.
    """
    recipients = db.query(TaskItemAlertRecipient).filter_by(item_id=alert.item_id).all()

    for recipient in recipients:
        assignments = db.query(TaskRoleAssignment).filter_by(
            task_role_id=recipient.task_role_id
        ).all()

        for assignment in assignments:
            delivery = TaskAlertDelivery(
                delivery_id=str(uuid.uuid4()),
                alert_id=alert.alert_id,
                user_id=assignment.user_id,
                client_id=assignment.client_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(delivery)

    logger.info(f"Alert deliveries created for alert {alert.alert_id}")
