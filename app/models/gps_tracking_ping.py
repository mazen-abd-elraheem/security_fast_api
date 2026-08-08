"""
SecureTrack Platform — GPS Tracking Ping Model
Stores periodic GPS pings from guard/outdoor users.
Used to calculate actual presence hours based on geofence proximity.
"""
from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class GpsTrackingPing(Base):
    __tablename__ = "gps_tracking_pings"
    __table_args__ = (
        Index('ix_pings_user_date', 'user_id', 'recorded_at'),
        Index('ix_pings_roster_fence', 'roster_id', 'is_within_geofence', 'recorded_at'),
    )

    ping_id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    roster_id = Column(String(36), ForeignKey("guard_roster.roster_id", ondelete="SET NULL"), nullable=True, index=True)

    # GPS coordinates sent by the device
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # Server-computed geofence status
    is_within_geofence = Column(Boolean, nullable=False, default=False)
    distance_meters = Column(Float, nullable=True)

    # Timestamp
    recorded_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    roster = relationship("GuardRoster", foreign_keys=[roster_id])

    def __repr__(self):
        return f"<GpsTrackingPing(ping_id={self.ping_id}, user={self.user_id}, in_geofence={self.is_within_geofence})>"
