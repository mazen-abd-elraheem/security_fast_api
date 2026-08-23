"""
SecureTrack Platform — Complaint Model
Tracks complaints submitted by Leaders (on behalf of guards) or directly by guards.
Flow: Guard/Leader → Leader reviews → HR escalation if unresolved.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        Index('ix_complaint_guard', 'guard_id'),
        Index('ix_complaint_submitted_by', 'submitted_by'),
        Index('ix_complaint_site', 'site_id'),
        Index('ix_complaint_status', 'status'),
    )

    complaint_id = Column(String(36), primary_key=True, index=True)

    # Who is the complaint about / for
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)  # Denormalized

    # Who submitted it (Leader on behalf of guard, or guard directly)
    submitted_by = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    submitted_by_name = Column(String(255), nullable=False)  # Denormalized
    submitted_by_role = Column(String(30), nullable=False)  # 'leader' or 'guard'

    # Site context
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    site_name = Column(String(255), nullable=False)  # Denormalized

    # Complaint details
    subject = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)  # e.g., salary, schedule, equipment, harassment

    # Status: pending → in_review → resolved / escalated_to_hr → hr_resolved
    status = Column(String(30), nullable=False, default="pending")

    # Resolution
    resolution_notes = Column(Text, nullable=True)
    resolved_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id])
    submitter = relationship("User", foreign_keys=[submitted_by])
    resolver = relationship("User", foreign_keys=[resolved_by])
    site = relationship("Site", foreign_keys=[site_id])

    def __repr__(self):
        return f"<Complaint(id={self.complaint_id}, guard={self.guard_name}, status={self.status})>"
