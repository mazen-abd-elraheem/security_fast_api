"""
SecureTrack Platform — Notification Model
Stores notifications for all users (visit alerts, attendance issues, system alerts).
"""
from sqlalchemy import Column, String, DateTime, Text, Boolean, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Notification(Base):
    """A notification for a user (visit required, attendance alert, system notification, etc.)."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index('ix_notif_user_read', 'user_id', 'is_read', 'created_at'),
    )

    notification_id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    # Notification content
    notif_type = Column(String(50), nullable=False, default="system")
    # Types: visit_required, visit_missed, attendance_alert, geofence_violation,
    #        incident_reported, incident_resolved, schedule_change, escalation, system
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)

    # Optional references for deep-linking
    reference_id = Column(String(36), nullable=True)   # site_id, visit_id, incident_id…
    reference_type = Column(String(50), nullable=True)  # "site", "visit", "incident", "route"

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="notifications")

    def __repr__(self):
        return f"<Notification({self.notification_id}, type={self.notif_type}, user={self.user_id})>"
