"""
SecureTrack Platform — Notification Routes
"""
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.notification import Notification
from app.enums import UserRole
from app.schemas.notification import NotificationResponse, NotificationListResponse, SendNotification

router = APIRouter()


def create_notification(
    db: Session,
    user_id: str,
    notif_type: str,
    title: str,
    message: str = None,
    reference_id: str = None,
    reference_type: str = None,
):
    """Helper function to create a notification."""
    notif = Notification(
        notification_id=str(uuid.uuid4()),
        user_id=user_id,
        notif_type=notif_type,
        title=title,
        message=message,
        reference_id=reference_id,
        reference_type=reference_type,
    )
    db.add(notif)
    db.flush()
    return notif


@router.get("", response_model=NotificationListResponse, summary="Get my notifications")
def get_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the authenticated user's notifications."""
    query = db.query(Notification).filter(Notification.user_id == current_user.user_id)
    total = query.count()
    unread = query.filter(Notification.is_read == False).count()
    notifs = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

    return NotificationListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in notifs],
        total=total,
        unread_count=unread,
    )


@router.put("/{notification_id}/read", summary="Mark as read")
def mark_as_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a notification as read."""
    notif = db.query(Notification).filter(
        Notification.notification_id == notification_id,
        Notification.user_id == current_user.user_id,
    ).first()
    if not notif:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"detail": "Marked as read"}


@router.put("/read-all", summary="Mark all as read")
def mark_all_as_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark all notifications as read."""
    db.query(Notification).filter(
        Notification.user_id == current_user.user_id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return {"detail": "All notifications marked as read"}


@router.post("/send", status_code=201, summary="Admin sends notification")
def send_notification(
    data: SendNotification,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Admin sends a notification to a specific user."""
    target = db.query(User).filter(User.user_id == data.target_user_id).first()
    if not target:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Target user not found")

    notif = create_notification(
        db, user_id=data.target_user_id,
        notif_type=data.notification_type, title=data.title,
        message=data.message, reference_id=data.reference_id,
        reference_type=data.reference_type,
    )
    db.commit()
    return {"detail": "Notification sent", "notification_id": notif.notification_id}
