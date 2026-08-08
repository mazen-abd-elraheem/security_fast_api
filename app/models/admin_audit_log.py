"""
SecureTrack Platform — Admin Audit Log Model
Tracks all administrative actions for accountability and compliance.
"""
from sqlalchemy import Column, String, DateTime, Text, JSON, Index
from datetime import datetime, timezone

from app.core.database import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index('ix_audit_date_action', 'created_at', 'action'),
    )

    log_id = Column(String(36), primary_key=True, index=True)
    admin_id = Column(String(36), nullable=False, index=True)
    admin_name = Column(String(255), nullable=False)

    # Action performed: user_created, user_deactivated, site_created, shift_modified,
    #                   roster_updated, route_assigned, incident_resolved, etc.
    action = Column(String(100), nullable=False, index=True)

    # Target entity
    target_type = Column(String(50), nullable=True)  # user, site, shift, roster, route, incident
    target_id = Column(String(36), nullable=True)
    target_name = Column(String(255), nullable=True)

    # Detailed description and metadata
    description = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)  # Extra context: old_value, new_value, reason, etc.

    # Severity: info, warning, critical
    severity = Column(String(20), nullable=False, default="info")

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AdminAuditLog(log_id={self.log_id}, action={self.action}, admin={self.admin_name})>"
