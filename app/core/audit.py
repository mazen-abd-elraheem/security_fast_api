"""
SecureTrack Platform — Centralized Audit Logger
Provides a reusable helper for logging all CUD + Read operations
with full before/after snapshots across every API endpoint.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

# Fields that MUST NEVER appear in audit logs (security-sensitive)
_EXCLUDED_FIELDS = frozenset({
    "password_hash", "password", "totp_secret",
    "fcm_token", "access_token", "refresh_token",
    "token", "secret", "otp",
})


def _sanitize_dict(data: dict) -> dict:
    """Remove security-sensitive fields from a dict before logging."""
    if not data:
        return {}
    return {
        k: "***REDACTED***" if k.lower() in _EXCLUDED_FIELDS else v
        for k, v in data.items()
    }


def _model_to_dict(obj) -> dict:
    """Convert a SQLAlchemy model instance to a dict for snapshotting."""
    if obj is None:
        return {}
    data = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name, None)
        if isinstance(val, datetime):
            val = val.isoformat()
        data[col.name] = val
    return _sanitize_dict(data)


def log_audit(
    db: Session,
    actor: User,
    action: str,
    target_type: str = None,
    target_id: str = None,
    target_name: str = None,
    description: str = None,
    old_values: dict = None,
    new_values: dict = None,
    severity: str = "info",
):
    """
    Create an audit log entry with optional before/after snapshots.

    Args:
        db: Database session
        actor: The user performing the action
        action: Action name (e.g. "user_created", "site_updated", "payroll_viewed")
        target_type: Entity type (e.g. "user", "site", "shift")
        target_id: ID of the target entity
        target_name: Human-readable name of the target
        description: Free-text description of what happened
        old_values: Snapshot of entity BEFORE the change (for updates/deletes)
        new_values: Snapshot of entity AFTER the change (for creates/updates)
        severity: "info", "warning", or "critical"
    """
    details = {}
    if old_values:
        details["before"] = _sanitize_dict(old_values)
    if new_values:
        details["after"] = _sanitize_dict(new_values)

    # For updates, compute a diff of changed fields
    if old_values and new_values:
        changed = {}
        for key in set(list(old_values.keys()) + list(new_values.keys())):
            if key.lower() in _EXCLUDED_FIELDS:
                continue
            old_val = old_values.get(key)
            new_val = new_values.get(key)
            if old_val != new_val:
                changed[key] = {"from": old_val, "to": new_val}
        if changed:
            details["changed_fields"] = changed

    log = AdminAuditLog(
        log_id=str(uuid.uuid4()),
        admin_id=actor.user_id,
        admin_name=actor.name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        description=description,
        details=details if details else None,
        severity=severity,
    )
    db.add(log)
    db.flush()
    return log


def log_create(db: Session, actor: User, target_type: str, obj, description: str = None):
    """Shorthand: log a CREATE operation with the new entity snapshot."""
    snapshot = _model_to_dict(obj)
    name = snapshot.get("name") or snapshot.get("title") or snapshot.get("employee_name") or str(snapshot.get("id", ""))
    obj_id = (
        getattr(obj, "user_id", None)
        or getattr(obj, f"{target_type}_id", None)
        or getattr(obj, "id", None)
        or snapshot.get(f"{target_type}_id")
        or ""
    )
    return log_audit(
        db, actor,
        action=f"{target_type}_created",
        target_type=target_type,
        target_id=str(obj_id),
        target_name=str(name),
        description=description or f"Created {target_type}: {name}",
        new_values=snapshot,
        severity="info",
    )


def log_update(db: Session, actor: User, target_type: str, old_snapshot: dict, obj, description: str = None):
    """Shorthand: log an UPDATE operation with before/after snapshots."""
    new_snapshot = _model_to_dict(obj)
    name = new_snapshot.get("name") or new_snapshot.get("title") or old_snapshot.get("name") or ""
    obj_id = (
        getattr(obj, "user_id", None)
        or getattr(obj, f"{target_type}_id", None)
        or getattr(obj, "id", None)
        or new_snapshot.get(f"{target_type}_id")
        or ""
    )
    return log_audit(
        db, actor,
        action=f"{target_type}_updated",
        target_type=target_type,
        target_id=str(obj_id),
        target_name=str(name),
        description=description or f"Updated {target_type}: {name}",
        old_values=old_snapshot,
        new_values=new_snapshot,
        severity="info",
    )


def log_delete(db: Session, actor: User, target_type: str, obj_or_snapshot, description: str = None):
    """Shorthand: log a DELETE operation with the deleted entity snapshot."""
    if isinstance(obj_or_snapshot, dict):
        snapshot = _sanitize_dict(obj_or_snapshot)
    else:
        snapshot = _model_to_dict(obj_or_snapshot)
    name = snapshot.get("name") or snapshot.get("title") or ""
    obj_id = snapshot.get("user_id") or snapshot.get(f"{target_type}_id") or snapshot.get("id") or ""
    return log_audit(
        db, actor,
        action=f"{target_type}_deleted",
        target_type=target_type,
        target_id=str(obj_id),
        target_name=str(name),
        description=description or f"Deleted {target_type}: {name}",
        old_values=snapshot,
        severity="critical",
    )


def log_read(db: Session, actor: User, target_type: str, target_id: str = None, target_name: str = None, description: str = None):
    """Shorthand: log a READ operation on sensitive data."""
    return log_audit(
        db, actor,
        action=f"{target_type}_viewed",
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        description=description or f"Viewed {target_type}: {target_name or target_id}",
        severity="info",
    )


def snapshot(obj) -> dict:
    """Take a snapshot of a model instance for later comparison."""
    return _model_to_dict(obj)
