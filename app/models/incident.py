"""
SecureTrack Platform — Incident Model
Field reports for security breaches, equipment damage, and other on-site events.
Category is now dynamic (FK to incident_categories).
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

    # Incident details — title is auto-derived from category name
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Dynamic category: stores category_id (UUID) + denormalized name for fast display
    category_id = Column(String(36), nullable=True, index=True)   # FK handled via seed (not enforced at DB level for flexibility)
    category = Column(String(100), nullable=False, default="other")  # denormalized category name

    # Severity: low, medium, high, critical — auto-derived from category
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
