"""
SecureTrack — Guard Evaluation Model
Supervisors evaluate guard performance quarterly/monthly.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base


class GuardEvaluation(Base):
    __tablename__ = "guard_evaluations"
    __table_args__ = (
        Index('ix_eval_guard', 'guard_id'),
        Index('ix_eval_period', 'period'),
    )

    eval_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)

    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    guard_name = Column(String(255), nullable=False)

    evaluator_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    evaluator_name = Column(String(255), nullable=True)

    period = Column(String(20), nullable=False)  # e.g. "2025-Q3", "2025-08"

    # Scores 1-5
    attendance_score = Column(Integer, nullable=False, default=3)
    punctuality_score = Column(Integer, nullable=False, default=3)
    appearance_score = Column(Integer, nullable=False, default=3)
    discipline_score = Column(Integer, nullable=False, default=3)
    communication_score = Column(Integer, nullable=False, default=3)

    overall_score = Column(Float, nullable=False, default=3.0)
    comments = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    guard = relationship("User", foreign_keys=[guard_id])
    evaluator = relationship("User", foreign_keys=[evaluator_id])
