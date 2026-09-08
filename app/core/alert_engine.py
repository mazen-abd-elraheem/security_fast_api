"""
SecureTrack Platform — Task Alert Engine
Handles alert creation, notification dispatch, and escalation for failed task items.

Flow:
  1. Leader submits task response with result='fail' or a note on an alertable item
  2. tasks.py creates a TaskAlert + TaskAlertDelivery records
  3. This engine dispatches notifications (DB + FCM push) to each recipient
  4. If not acknowledged within escalation_minutes, escalates to higher roles
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.task_models import (
    TaskAlert, TaskAlertDelivery, TaskInstance,
    TaskItem, TaskItemAlertRecipient, TaskRoleAssignment,
    TaskRole, ClientAccount,
)
from app.models.user import User
from app.models.notification import Notification
from app.enums import TaskAlertStatus

logger = logging.getLogger(__name__)


def dispatch_alert_notifications(db: Session, alert: TaskAlert) -> int:
    """
    Create DB notifications + push data for each alert delivery recipient.
    Returns the number of notifications dispatched.
    """
    deliveries = db.query(TaskAlertDelivery).filter_by(alert_id=alert.alert_id).all()
    item = db.query(TaskItem).filter_by(item_id=alert.item_id).first()
    instance = db.query(TaskInstance).filter_by(instance_id=alert.instance_id).first()

    item_title = item.title if item else "Unknown item"
    alert_title = f"⚠️ Task Alert: {item_title}"
    alert_message = (
        f"A task item has been marked as {'WRONG' if alert.alert_type == 'wrong' else 'requires attention'}. "
        f"Please review and acknowledge."
    )

    count = 0
    for delivery in deliveries:
        if delivery.user_id:
            # Internal user notification (DB record)
            notif = Notification(
                notification_id=str(uuid.uuid4()),
                user_id=delivery.user_id,
                notif_type="task_alert",
                title=alert_title,
                message=alert_message,
                reference_id=alert.alert_id,
                reference_type="task_alert",
            )
            db.add(notif)
            delivery.delivered_at = datetime.now(timezone.utc)
            count += 1

            # Push notification data (for Flutter alarm screen)
            _send_fcm_data_to_user(db, delivery.user_id, alert, item_title)

        elif delivery.client_id:
            # Client notification — we store a delivery record but
            # client notifications use a separate polling endpoint
            delivery.delivered_at = datetime.now(timezone.utc)
            count += 1

            _send_fcm_data_to_client(db, delivery.client_id, alert, item_title)

    db.commit()
    logger.info(f"Dispatched {count} notifications for alert {alert.alert_id}")
    return count


def _send_fcm_data_to_user(db: Session, user_id: str, alert: TaskAlert, item_title: str):
    """
    Send FCM data message to an internal user.
    Data-only messages allow the Flutter app to trigger full-screen alarms.
    """
    user = db.query(User).filter_by(user_id=user_id).first()
    if not user or not user.fcm_token:
        logger.debug(f"No FCM token for user {user_id}, skipping push")
        return

    try:
        _send_fcm_data_message(
            token=user.fcm_token,
            data={
                "type": "task_alert",
                "alert_id": alert.alert_id,
                "alert_type": alert.alert_type,
                "item_title": item_title,
                "instance_id": alert.instance_id,
                "severity": "critical",  # Triggers alarm mode in Flutter
                "sound": "alarm",
                "vibrate": "true",
                "fullscreen": "true",
            },
        )
    except Exception as e:
        logger.warning(f"FCM send failed for user {user_id}: {e}")


def _send_fcm_data_to_client(db: Session, client_id: str, alert: TaskAlert, item_title: str):
    """Send FCM data message to a client account."""
    client = db.query(ClientAccount).filter_by(client_id=client_id).first()
    if not client or not client.fcm_token:
        logger.debug(f"No FCM token for client {client_id}, skipping push")
        return

    try:
        _send_fcm_data_message(
            token=client.fcm_token,
            data={
                "type": "task_alert",
                "alert_id": alert.alert_id,
                "alert_type": alert.alert_type,
                "item_title": item_title,
                "instance_id": alert.instance_id,
                "severity": "high",
                "sound": "alert",
                "vibrate": "true",
                "fullscreen": "false",
            },
        )
    except Exception as e:
        logger.warning(f"FCM send failed for client {client_id}: {e}")


def _send_fcm_data_message(token: str, data: dict):
    """
    Send a data-only FCM message via Firebase Admin SDK.
    Data-only messages bypass the system notification tray and allow
    the Flutter app to handle display (full-screen alarm, sound, etc.).

    Requires firebase_admin to be initialised in app startup.
    Falls back gracefully if Firebase is not configured.
    """
    try:
        import firebase_admin
        from firebase_admin import messaging

        if not firebase_admin._apps:
            logger.info("Firebase Admin not initialised — skipping FCM push")
            return

        message = messaging.Message(
            data={k: str(v) for k, v in data.items()},
            token=token,
            android=messaging.AndroidConfig(
                priority="high",
                ttl=300,  # 5 minutes TTL
            ),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        content_available=True,
                        sound="alarm.wav",
                    ),
                ),
            ),
        )
        response = messaging.send(message)
        logger.info(f"FCM sent: {response}")
    except ImportError:
        logger.info("firebase_admin not installed — FCM push disabled")
    except Exception as e:
        logger.warning(f"FCM send error: {e}")


def check_escalations(db: Session) -> int:
    """
    Check for unacknowledged alerts past their escalation window.
    Called periodically (e.g. by a background scheduler).
    Returns count of escalated alerts.
    """
    from datetime import timedelta

    pending_alerts = db.query(TaskAlert).filter(
        TaskAlert.status == TaskAlertStatus.PENDING,
        TaskAlert.escalation_minutes.isnot(None),
    ).all()

    count = 0
    now = datetime.now(timezone.utc)

    for alert in pending_alerts:
        escalation_deadline = alert.created_at + timedelta(minutes=alert.escalation_minutes)
        if now > escalation_deadline:
            alert.status = TaskAlertStatus.ESCALATED
            alert.escalated_at = now
            count += 1

            # Create escalation notification for admins
            admins = db.query(User).filter(
                User.role == "admin",
                User.is_active == True,
            ).all()
            for admin in admins:
                item = db.query(TaskItem).filter_by(item_id=alert.item_id).first()
                notif = Notification(
                    notification_id=str(uuid.uuid4()),
                    user_id=admin.user_id,
                    notif_type="task_escalation",
                    title=f"🔴 ESCALATED: {item.title if item else 'Unknown'}",
                    message="Alert was not acknowledged within the escalation window. Requires immediate attention.",
                    reference_id=alert.alert_id,
                    reference_type="task_alert",
                )
                db.add(notif)

            logger.warning(f"Alert {alert.alert_id} escalated — not acknowledged after {alert.escalation_minutes} min")

    if count:
        db.commit()
    logger.info(f"Escalation check: {count} alerts escalated out of {len(pending_alerts)} pending")
    return count


def get_alert_stats(db: Session) -> dict:
    """Dashboard stats for the alert system."""
    total = db.query(TaskAlert).count()
    pending = db.query(TaskAlert).filter(TaskAlert.status == TaskAlertStatus.PENDING).count()
    acknowledged = db.query(TaskAlert).filter(TaskAlert.status == TaskAlertStatus.ACKNOWLEDGED).count()
    escalated = db.query(TaskAlert).filter(TaskAlert.status == TaskAlertStatus.ESCALATED).count()

    return {
        "total_alerts": total,
        "pending": pending,
        "acknowledged": acknowledged,
        "escalated": escalated,
    }
