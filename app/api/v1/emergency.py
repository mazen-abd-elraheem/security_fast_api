"""
SecureTrack Platform — Emergency Alert Routes
Handles panic/SOS emergency alerts — always CRITICAL severity, no category selection required.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import uuid

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.incident import Incident
from app.models.notification import Notification
from app.enums import UserRole
from app.schemas.incident import IncidentResponse
from app.api.v1.incidents import _build_response

router = APIRouter()

_EMERGENCY_MSG = (
    "⚠️ EMERGENCY ALERT: An emergency has been triggered by a leader. "
    "All supervisors and admins must respond immediately."
)

_EMERGENCY_CORRECTIVE = (
    "EMERGENCY PROTOCOL ACTIVE — Stay calm. Await instructions from your supervisor. "
    "Do not leave your post unless directed to do so."
)


def _send_emergency_alerts(db: Session, incident: Incident, reporter_name: str):
    """Blast in-app notifications to ALL admins and supervisors."""
    recipients = db.query(User).filter(
        User.role.in_(["admin", "supervisor"]),
        User.is_active == True,
    ).all()

    for user in recipients:
        notif = Notification(
            notification_id=str(uuid.uuid4()),
            user_id=user.user_id,
            notif_type="emergency_alert",
            title=f"🚨🚨 EMERGENCY ALERT by {reporter_name}",
            message=_EMERGENCY_MSG,
            reference_id=incident.incident_id,
            reference_type="emergency",
        )
        db.add(notif)
    db.commit()


@router.post(
    "",
    response_model=IncidentResponse,
    status_code=201,
    summary="Trigger Emergency Alert",
)
def trigger_emergency(
    current_user: User = Depends(require_role(UserRole.LEADER)),
    db: Session = Depends(get_db),
):
    """
    One-tap CRITICAL emergency alert from a Leader.
    - No category or site input required.
    - Always severity=critical, status=open.
    - Blasts notifications to all admins and supervisors.
    """
    # Create the emergency incident record
    # Use reporter's first associated site if available, else None
    site_id = None
    site_name_fallback = "Emergency Location"

    db_incident = Incident(
        incident_id=str(uuid.uuid4()),
        site_id=site_id or "00000000-0000-0000-0000-000000000000",  # placeholder
        reported_by=current_user.user_id,
        title="EMERGENCY ALERT",
        description=f"Emergency triggered by {current_user.name}. Immediate response required.",
        category="emergency",
        category_id=None,
        severity="critical",
        status="open",
        photo_url=None,
    )
    db.add(db_incident)
    db.commit()
    db.refresh(db_incident)

    # Blast notifications
    _send_emergency_alerts(db, db_incident, current_user.name or current_user.user_id)

    response = _build_response(db_incident, corrective_action=_EMERGENCY_CORRECTIVE)
    return response


@router.get(
    "",
    response_model=dict,
    summary="List emergency alerts (admin only)",
)
def list_emergencies(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """List all critical emergency alerts for admin review."""
    incidents = (
        db.query(Incident)
        .filter(Incident.category == "emergency")
        .order_by(Incident.created_at.desc())
        .limit(50)
        .all()
    )
    items = []
    for i in incidents:
        items.append({
            "incident_id": i.incident_id,
            "reported_by": i.reported_by,
            "reporter_name": i.reporter.name if i.reporter else "Unknown",
            "title": i.title,
            "description": i.description,
            "severity": i.severity,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        })
    return {"emergencies": items, "total": len(items)}


@router.put(
    "/{incident_id}/resolve",
    response_model=dict,
    summary="Resolve emergency alert (admin only)",
)
def resolve_emergency(
    incident_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Mark an emergency incident as resolved."""
    from datetime import datetime, timezone
    incident = db.query(Incident).filter(
        Incident.incident_id == incident_id,
        Incident.category == "emergency",
    ).first()
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Emergency not found")

    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Emergency resolved", "incident_id": incident_id}
