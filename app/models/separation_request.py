"""
SecureTrack Platform — Separation Request Model
Handles Resignation, Termination, and Exclusion workflows.
Flow: Leader → Supervisor (must confirm uniform return) → Ops Manager → HR (final + financial settlement).
"""
from sqlalchemy import Column, String, Boolean, Float, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class SeparationRequest(Base):
    __tablename__ = "separation_requests"
    __table_args__ = (
        Index('ix_separation_user', 'user_id'),
        Index('ix_separation_site', 'site_id'),
        Index('ix_separation_status', 'status'),
        Index('ix_separation_type', 'separation_type'),
    )

    separation_id = Column(String(36), primary_key=True, index=True)

    # Who is being separated
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    user_name = Column(String(255), nullable=False)  # Denormalized
    employee_code = Column(String(50), nullable=True)  # Denormalized

    # Site context
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    site_name = Column(String(255), nullable=False)  # Denormalized

    # Separation type: resignation / termination / exclusion
    separation_type = Column(String(30), nullable=False)

    # Reason
    reason = Column(Text, nullable=False)

    # Multi-step approval status:
    # pending_leader → pending_supervisor → pending_ops_mgr → pending_hr → completed / rejected
    status = Column(String(30), nullable=False, default="pending_leader")

    # Who initiated (usually Leader)
    initiated_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    initiated_by_name = Column(String(255), nullable=True)

    # Supervisor review — MUST confirm uniform return before forwarding
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_notes = Column(Text, nullable=True)
    supervisor_reviewed_at = Column(DateTime, nullable=True)
    uniform_returned = Column(Boolean, nullable=False, default=False)
    uniform_return_confirmed_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)

    # Ops Manager review
    ops_manager_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    ops_manager_notes = Column(Text, nullable=True)
    ops_manager_reviewed_at = Column(DateTime, nullable=True)

    # HR final decision
    hr_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    hr_notes = Column(Text, nullable=True)
    hr_reviewed_at = Column(DateTime, nullable=True)

    # Financial settlement
    financial_settlement = Column(Float, nullable=True)  # Final amount owed/deducted
    assets_returned = Column(Boolean, nullable=False, default=False)  # All company assets returned

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    initiator = relationship("User", foreign_keys=[initiated_by])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    uniform_confirmer = relationship("User", foreign_keys=[uniform_return_confirmed_by])
    ops_manager = relationship("User", foreign_keys=[ops_manager_id])
    hr = relationship("User", foreign_keys=[hr_id])
    site = relationship("Site", foreign_keys=[site_id])

    def __repr__(self):
        return f"<SeparationRequest(id={self.separation_id}, user={self.user_name}, type={self.separation_type}, status={self.status})>"
