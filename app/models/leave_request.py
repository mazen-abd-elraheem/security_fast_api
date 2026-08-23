"""
SecureTrack Platform — Leave Request Model
Tracks leave requests submitted by Leaders (on behalf of guards) or directly by guards.
Normal leave: Guard → Leader → Supervisor (final).
Exceptional leave: Guard → Leader → Supervisor → Ops Manager → HR.
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index, Date
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (
        Index('ix_leave_guard', 'guard_id'),
        Index('ix_leave_submitted_by', 'submitted_by'),
        Index('ix_leave_site', 'site_id'),
        Index('ix_leave_status', 'status'),
        Index('ix_leave_type', 'leave_type'),
    )

    leave_id = Column(String(36), primary_key=True, index=True)

    # Who is the leave for
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)  # Denormalized

    # Who submitted it (Leader on behalf of guard, or guard directly)
    submitted_by = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    submitted_by_name = Column(String(255), nullable=False)  # Denormalized

    # Site context
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    site_name = Column(String(255), nullable=False)  # Denormalized

    # Leave details
    leave_type = Column(String(30), nullable=False, default="normal")  # normal / exceptional
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)

    # Multi-step approval status:
    # pending → approved_by_leader → approved_by_supervisor (final for normal)
    #        → approved_by_ops_mgr → approved_by_hr (for exceptional)
    #        → rejected (at any step)
    status = Column(String(30), nullable=False, default="pending")

    # Approval chain
    leader_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    leader_notes = Column(Text, nullable=True)
    leader_reviewed_at = Column(DateTime, nullable=True)

    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_notes = Column(Text, nullable=True)
    supervisor_reviewed_at = Column(DateTime, nullable=True)

    ops_manager_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    ops_manager_notes = Column(Text, nullable=True)
    ops_manager_reviewed_at = Column(DateTime, nullable=True)

    hr_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    hr_notes = Column(Text, nullable=True)
    hr_reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id])
    submitter = relationship("User", foreign_keys=[submitted_by])
    leader = relationship("User", foreign_keys=[leader_id])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    ops_manager = relationship("User", foreign_keys=[ops_manager_id])
    hr = relationship("User", foreign_keys=[hr_id])
    site = relationship("Site", foreign_keys=[site_id])

    def __repr__(self):
        return f"<LeaveRequest(id={self.leave_id}, guard={self.guard_name}, type={self.leave_type}, status={self.status})>"
