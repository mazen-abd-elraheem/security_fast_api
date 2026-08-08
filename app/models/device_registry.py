"""
SecureTrack Platform — Device Registry Model
Tracks trusted devices for anti-sharing and device fingerprinting.
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class DeviceRegistry(Base):
    __tablename__ = "device_registry"
    __table_args__ = (
        Index('ix_devices_user_trusted', 'user_id', 'is_trusted'),
    )

    registry_id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    # Device identification
    device_id = Column(String(255), nullable=False, index=True)  # IMEI or unique device identifier
    device_model = Column(String(255), nullable=True)  # e.g., "Samsung Galaxy S23"
    os_version = Column(String(100), nullable=True)  # e.g., "Android 14"

    # Trust status
    is_trusted = Column(Boolean, nullable=False, default=True)

    # Timestamps
    registered_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="devices", foreign_keys=[user_id])

    def __repr__(self):
        return f"<DeviceRegistry(registry_id={self.registry_id}, user={self.user_id}, device={self.device_id})>"
