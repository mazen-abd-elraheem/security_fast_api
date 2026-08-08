"""
SecureTrack Platform — Device Service
Device registration and trust verification for anti-sharing.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.device_registry import DeviceRegistry
from app.schemas.device import DeviceRegisterRequest
from app.core.config import get_settings
from app.core.exceptions import NotFoundException, BadRequestException

settings = get_settings()


class DeviceService:
    """Device registration and trust verification."""

    @staticmethod
    def register_device(db: Session, user_id: str, device_data: DeviceRegisterRequest) -> DeviceRegistry:
        """Register a new device for a user."""
        # Check if device already registered for this user
        existing = db.query(DeviceRegistry).filter(
            DeviceRegistry.user_id == user_id,
            DeviceRegistry.device_id == device_data.device_id,
        ).first()
        if existing:
            # Update last seen
            existing.last_seen_at = datetime.now(timezone.utc)
            existing.device_model = device_data.device_model or existing.device_model
            existing.os_version = device_data.os_version or existing.os_version
            db.commit()
            db.refresh(existing)
            return existing

        # Check max devices
        device_count = db.query(DeviceRegistry).filter(
            DeviceRegistry.user_id == user_id,
            DeviceRegistry.is_trusted == True,
        ).count()
        if device_count >= settings.MAX_TRUSTED_DEVICES_PER_USER:
            raise BadRequestException(
                f"Maximum {settings.MAX_TRUSTED_DEVICES_PER_USER} trusted devices allowed. "
                "Remove an existing device first."
            )

        db_device = DeviceRegistry(
            registry_id=str(uuid.uuid4()),
            user_id=user_id,
            device_id=device_data.device_id,
            device_model=device_data.device_model,
            os_version=device_data.os_version,
            registered_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(db_device)
        db.commit()
        db.refresh(db_device)
        return db_device

    @staticmethod
    def get_user_devices(db: Session, user_id: str) -> list:
        """Get all devices for a user."""
        return db.query(DeviceRegistry).filter(
            DeviceRegistry.user_id == user_id,
        ).order_by(DeviceRegistry.last_seen_at.desc()).all()

    @staticmethod
    def remove_device(db: Session, registry_id: str) -> None:
        """Remove a device registration."""
        device = db.query(DeviceRegistry).filter(DeviceRegistry.registry_id == registry_id).first()
        if not device:
            raise NotFoundException("Device", registry_id)
        db.delete(device)
        db.commit()

    @staticmethod
    def update_last_seen(db: Session, user_id: str, device_id: str) -> None:
        """Update last_seen timestamp for a device."""
        device = db.query(DeviceRegistry).filter(
            DeviceRegistry.user_id == user_id,
            DeviceRegistry.device_id == device_id,
        ).first()
        if device:
            device.last_seen_at = datetime.now(timezone.utc)
            db.commit()
