"""
SecureTrack — Disciplinary Action Model
HR tracks warnings, deductions, and suspensions for guards.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship
from app.core.database import Base


class DisciplinaryAction(Base):
    __tablename__ = "disciplinary_actions"
    __table_args__ = (
        Index('ix_disciplinary_guard', 'guard_id'),
        Index('ix_disciplinary_type', 'action_type'),
        Index('ix_disciplinary_status', 'status'),
    )

    action_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    # Target guard
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)
    guard_code = Column(String(50), nullable=True)

    # Type: warning, deduction, suspension
    action_type = Column(String(30), nullable=False)

    # Severity: minor, moderate, major, critical
    severity = Column(String(20), nullable=False, default="moderate")

    # Details
    reason = Column(Text, nullable=False)
    reference_number = Column(String(100), nullable=True)

    # For deductions
    deduction_amount = Column(Float, nullable=True)
    deduction_days = Column(Integer, nullable=True)

    # For suspensions
    suspension_start = Column(DateTime, nullable=True)
    suspension_end = Column(DateTime, nullable=True)

    # Status: active, appealed, revoked
    status = Column(String(20), nullable=False, default="active")

    # Who issued it
    issued_by = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    issued_by_name = Column(String(255), nullable=True)

    # Linked to payroll
    linked_to_payroll = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id])
    issuer = relationship("User", foreign_keys=[issued_by])

    def __repr__(self):
        return f"<DisciplinaryAction({self.action_id}, type={self.action_type}, guard={self.guard_id})>"
