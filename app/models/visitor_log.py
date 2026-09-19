"""
SecureTrack Platform — Visitor Log Models
Leader records visitors at a site. Visit reasons are admin-managed.
"""
import uuid
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class VisitReason(Base):
    """Admin-managed dropdown of allowed visit reasons per tenant."""
    __tablename__ = "visit_reasons"
    __table_args__ = (
        Index("ix_visit_reasons_tenant", "tenant_id"),
    )

    reason_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    label = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    def __repr__(self):
        return f"<VisitReason(id={self.reason_id}, label={self.label})>"


class VisitorLog(Base):
    """A visitor entry recorded by a leader at a site."""
    __tablename__ = "visitor_logs"
    __table_args__ = (
        Index("ix_visitor_logs_tenant", "tenant_id"),
        Index("ix_visitor_logs_leader", "leader_id"),
        Index("ix_visitor_logs_site", "site_id"),
        Index("ix_visitor_logs_created", "created_at"),
    )

    log_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    leader_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    leader_name = Column(String(255), nullable=False)   # Denormalized
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="SET NULL"), nullable=True)
    site_name = Column(String(255), nullable=True)      # Denormalized

    # Visitor info
    visitor_name = Column(String(255), nullable=False)
    visitor_id_number = Column(String(100), nullable=True)   # Passport / National ID
    visit_reason = Column(String(100), nullable=False)        # From VisitReason.label
    visitor_photo_url = Column(String(512), nullable=True)    # Optional photo
    id_photo_url = Column(String(512), nullable=True)         # Optional ID scan
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    leader = relationship("User", foreign_keys=[leader_id])
    site = relationship("Site", foreign_keys=[site_id])

    def __repr__(self):
        return f"<VisitorLog(id={self.log_id}, visitor={self.visitor_name}, reason={self.visit_reason})>"
