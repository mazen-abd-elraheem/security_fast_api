"""
SecureTrack Platform — Incident Model
Field reports for security breaches, equipment damage, and other on-site events.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index('ix_incidents_site_status', 'site_id', 'status'),
        Index('ix_incidents_date_status', 'created_at', 'status'),
    )

    incident_id = Column(String(36), primary_key=True, index=True)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False, index=True)
    reported_by = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    visit_id = Column(String(36), ForeignKey("supervisor_visits.visit_id", ondelete="SET NULL"), nullable=True, index=True)

    # Incident details
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Category: equipment_damage, security_breach, unauthorized_access, etc.
    category = Column(String(50), nullable=False, default="other")

    # Severity: low, medium, high, critical
    severity = Column(String(20), nullable=False, default="medium")

    # Status: open, investigating, resolved, closed
    status = Column(String(20), nullable=False, default="open")

    # Photo evidence
    photo_url = Column(String(500), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    site = relationship("Site", back_populates="incidents")
    reporter = relationship("User", foreign_keys=[reported_by])

    def __repr__(self):
        return f"<Incident(incident_id={self.incident_id}, title={self.title}, severity={self.severity})>"
