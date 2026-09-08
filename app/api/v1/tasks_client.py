"""
SecureTrack Platform — Client (Tenant) Task API
Tenant-scoped endpoints for client users (bank staff).
All queries are automatically scoped to the client's tenant via JWT tenant_id.
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, verify_password
from app.models.task_models import (
    Tenant, TenantSiteAccess, ClientAccount,
    TaskRole, TaskRoleAssignment,
    TaskTemplate, TaskSection, TaskItem,
    TaskInstance, TaskResponse,
    TaskAlert, TaskAlertDelivery,
)
from app.schemas.task_schemas import (
    ClientLoginRequest, ClientLoginResponse, ClientAccountOut,
    TaskInstanceOut, TaskInstanceReview, TaskResponseOut,
    TaskAlertOut,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ══════════════════════════════════════════════
# CLIENT AUTHENTICATION
# ══════════════════════════════════════════════

def _get_current_client(
    db: Session,
    token_payload: dict,
) -> ClientAccount:
    """Extract client account from JWT payload."""
    client_id = token_payload.get("sub")
    tenant_id = token_payload.get("tenant_id")
    if not client_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid client token")

    client = db.query(ClientAccount).filter_by(
        client_id=client_id, tenant_id=tenant_id
    ).first()
    if not client:
        raise HTTPException(status_code=401, detail="Client account not found")
    if client.status != "active":
        raise HTTPException(status_code=403, detail="Client account is not active")
    return client


def get_client_from_token(
    db: Session = Depends(get_db),
):
    """
    FastAPI dependency — extracts client from Bearer token.
    Client tokens have type='client_access' and include tenant_id.
    """
    from fastapi import Request
    from app.core.security import verify_token as _verify

    def _inner(request: Request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authorization header")
        token = auth_header.split(" ", 1)[1]
        payload = _verify(token, expected_type="client_access")
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired client token")
        return _get_current_client(db, payload)

    return Depends(_inner)


# Simpler dependency
from fastapi.security import OAuth2PasswordBearer
from app.core.security import verify_token

_client_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/tasks/client/login")


def get_current_client(
    token: str = Depends(_client_oauth2),
    db: Session = Depends(get_db),
) -> ClientAccount:
    """Dependency — extracts current client from JWT token."""
    payload = verify_token(token, expected_type="client_access")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired client token")
    return _get_current_client(db, payload)


@router.post("/login", response_model=ClientLoginResponse)
def client_login(
    body: ClientLoginRequest,
    db: Session = Depends(get_db),
):
    """Client (bank user) login — returns JWT with tenant_id."""
    client = db.query(ClientAccount).filter_by(
        email=body.email, tenant_id=body.tenant_id
    ).first()
    if not client:
        raise HTTPException(status_code=401, detail="Invalid email or tenant")

    if not verify_password(body.password, client.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    if client.status == "pending_approval":
        raise HTTPException(status_code=403, detail="Account pending admin approval")
    if client.status != "active":
        raise HTTPException(status_code=403, detail="Account is deactivated")

    # Create client-specific JWT tokens
    token_data = {
        "sub": client.client_id,
        "tenant_id": client.tenant_id,
        "type": "client_access",
        "role": "client",
    }

    from jose import jwt as jose_jwt
    from app.core.config import get_settings
    settings = get_settings()

    # Access token (8 hours)
    access_expire = datetime.now(timezone.utc) + timedelta(hours=8)
    access_payload = {**token_data, "exp": access_expire, "jti": str(uuid.uuid4()),
                      "iat": datetime.now(timezone.utc), "type": "client_access"}
    access_token = jose_jwt.encode(access_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    # Refresh token (7 days)
    refresh_expire = datetime.now(timezone.utc) + timedelta(days=7)
    refresh_payload = {**token_data, "exp": refresh_expire, "jti": str(uuid.uuid4()),
                       "iat": datetime.now(timezone.utc), "type": "client_refresh"}
    refresh_token = jose_jwt.encode(refresh_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    logger.info(f"Client login: {client.email} (tenant: {client.tenant_id})")

    return ClientLoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        client=ClientAccountOut.model_validate(client),
    )


@router.post("/register", response_model=ClientAccountOut, status_code=201)
def client_self_register(
    body: ClientLoginRequest,
    name: str = Query(..., min_length=1),
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Client self-registration — account is created with pending_approval status."""
    tenant = db.query(Tenant).filter_by(tenant_id=body.tenant_id).first()
    if not tenant or tenant.status != "active":
        raise HTTPException(status_code=404, detail="Tenant not found or inactive")

    exists = db.query(ClientAccount).filter_by(
        tenant_id=body.tenant_id, email=body.email
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Email already registered")

    from app.core.security import hash_password
    client = ClientAccount(
        client_id=str(uuid.uuid4()),
        tenant_id=body.tenant_id,
        name=name,
        email=body.email,
        phone_number=phone,
        password_hash=hash_password(body.password),
        status="pending_approval",  # Requires admin activation
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    logger.info(f"Client self-registered: {client.email} (pending approval)")
    return client


# ══════════════════════════════════════════════
# CLIENT TASK VIEWS (Tenant-scoped)
# ══════════════════════════════════════════════

def _get_tenant_site_ids(db: Session, tenant_id: str) -> List[str]:
    """Get all site IDs accessible by a tenant."""
    accesses = db.query(TenantSiteAccess).filter_by(tenant_id=tenant_id).all()
    return [a.site_id for a in accesses]


@router.get("/instances", response_model=List[TaskInstanceOut])
def client_list_instances(
    status_filter: Optional[str] = None,
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """View task instances scoped to the client's tenant sites."""
    site_ids = _get_tenant_site_ids(db, client.tenant_id)
    if not site_ids:
        return []

    q = db.query(TaskInstance).filter(TaskInstance.site_id.in_(site_ids))
    if status_filter:
        q = q.filter(TaskInstance.status == status_filter)

    instances = q.order_by(TaskInstance.created_at.desc()).all()
    return [_load_client_instance(db, i) for i in instances]


@router.get("/instances/{instance_id}", response_model=TaskInstanceOut)
def client_get_instance(
    instance_id: str,
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """View a specific task instance (must be in client's sites)."""
    site_ids = _get_tenant_site_ids(db, client.tenant_id)
    instance = db.query(TaskInstance).filter_by(instance_id=instance_id).first()
    if not instance or instance.site_id not in site_ids:
        raise HTTPException(status_code=404, detail="Instance not found")
    return _load_client_instance(db, instance)


@router.post("/instances/{instance_id}/review")
def client_review_instance(
    instance_id: str,
    body: TaskInstanceReview,
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Client approves or rejects a completed task instance."""
    # Check permissions
    _check_client_permission(db, client, "tasks.approve")

    site_ids = _get_tenant_site_ids(db, client.tenant_id)
    instance = db.query(TaskInstance).filter_by(instance_id=instance_id).first()
    if not instance or instance.site_id not in site_ids:
        raise HTTPException(status_code=404, detail="Instance not found")

    if instance.status != "completed":
        raise HTTPException(status_code=400, detail="Can only review completed tasks")

    instance.review_status = body.review_status
    instance.reviewed_by = client.client_id
    instance.review_note = body.review_note
    instance.reviewed_at = datetime.now(timezone.utc)
    instance.status = "reviewed"
    db.commit()

    logger.info(f"Task {instance_id} reviewed by client {client.client_id}: {body.review_status}")
    return {"message": f"Task {body.review_status}"}


@router.get("/alerts")
def client_alerts(
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """View alerts for the client's sites."""
    site_ids = _get_tenant_site_ids(db, client.tenant_id)
    if not site_ids:
        return []

    # Get instances in client's sites that have alerts
    instance_ids = [i.instance_id for i in
                    db.query(TaskInstance).filter(TaskInstance.site_id.in_(site_ids)).all()]
    if not instance_ids:
        return []

    alerts = db.query(TaskAlert).filter(
        TaskAlert.instance_id.in_(instance_ids)
    ).order_by(TaskAlert.created_at.desc()).all()

    results = []
    for alert in alerts:
        item = db.query(TaskItem).filter_by(item_id=alert.item_id).first()
        results.append({
            "alert_id": alert.alert_id,
            "alert_type": alert.alert_type,
            "status": alert.status,
            "item_title": item.title if item else None,
            "instance_id": alert.instance_id,
            "created_at": alert.created_at.isoformat(),
        })
    return results


@router.get("/alerts/pending")
def client_pending_alerts(
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Get unacknowledged alerts for the current client user."""
    deliveries = db.query(TaskAlertDelivery).filter(
        TaskAlertDelivery.client_id == client.client_id,
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
def client_acknowledge_alert(
    alert_id: str,
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Client acknowledges an alert."""
    delivery = db.query(TaskAlertDelivery).filter(
        TaskAlertDelivery.alert_id == alert_id,
        TaskAlertDelivery.client_id == client.client_id,
    ).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Alert delivery not found")

    delivery.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Alert acknowledged"}


@router.get("/reports")
def client_reports(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Client analytics — task completion stats for their sites."""
    site_ids = _get_tenant_site_ids(db, client.tenant_id)
    if not site_ids:
        return {"total": 0, "completed": 0, "pending": 0, "failed_items": 0, "pass_rate": 0}

    q = db.query(TaskInstance).filter(TaskInstance.site_id.in_(site_ids))

    if date_from:
        q = q.filter(TaskInstance.created_at >= date_from)
    if date_to:
        q = q.filter(TaskInstance.created_at <= date_to)

    instances = q.all()
    total = len(instances)
    completed = sum(1 for i in instances if i.status in ("completed", "reviewed"))
    pending = sum(1 for i in instances if i.status == "pending")

    # Count failed items across all responses
    instance_ids = [i.instance_id for i in instances]
    failed_items = 0
    total_items = 0
    if instance_ids:
        responses = db.query(TaskResponse).filter(
            TaskResponse.instance_id.in_(instance_ids)
        ).all()
        total_items = len(responses)
        failed_items = sum(1 for r in responses if r.result == "fail")

    pass_rate = ((total_items - failed_items) / total_items * 100) if total_items > 0 else 0

    return {
        "total_instances": total,
        "completed": completed,
        "pending": pending,
        "total_items_responded": total_items,
        "failed_items": failed_items,
        "pass_rate": round(pass_rate, 1),
    }


@router.put("/fcm-token")
def update_client_fcm_token(
    fcm_token: str = Query(...),
    client: ClientAccount = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Update FCM token for push notifications."""
    client.fcm_token = fcm_token
    db.commit()
    return {"message": "FCM token updated"}


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════

def _check_client_permission(db: Session, client: ClientAccount, permission: str):
    """Check if a client has a specific task permission."""
    assignments = db.query(TaskRoleAssignment).filter_by(client_id=client.client_id).all()
    for assignment in assignments:
        role = db.query(TaskRole).filter_by(role_id=assignment.task_role_id).first()
        if role and permission in (role.permissions or []):
            return True
    raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")


def _load_client_instance(db: Session, instance: TaskInstance) -> TaskInstanceOut:
    """Load instance details for client view."""
    from app.models.user import User
    from app.models.site import Site

    assignee = db.query(User).filter_by(user_id=instance.assigned_to).first()
    template = db.query(TaskTemplate).filter_by(template_id=instance.template_id).first()
    site = db.query(Site).filter_by(site_id=instance.site_id).first() if instance.site_id else None

    responses = db.query(TaskResponse).filter_by(instance_id=instance.instance_id).all()
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
