from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Integer, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.core.database import Base


class VacationRequest(Base):
    __tablename__ = "vacation_requests"
    __table_args__ = (
        Index('ix_vacation_guard', 'user_id'),
        Index('ix_vacation_supervisor', 'supervisor_id'),
        Index('ix_vacation_status', 'status'),
    )

    request_id = Column(String(36), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    
    # Target User (Guard / Supervisor / Outdoor / Lady)
    user_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    user_name = Column(String(255), nullable=False)
    employee_code = Column(String(50), nullable=True)
    
    # Context
    site_id = Column(String(36), nullable=False)
    site_name = Column(String(255), nullable=False)
    
    # Creator
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_name = Column(String(255), nullable=True)
    
    # Vacation Details
    days_count = Column(Integer, nullable=False, default=1)
    image_url = Column(String(500), nullable=True)  # URL to uploaded image
    
    # Approval chain
    # pending -> approved -> rejected
    status = Column(String(30), nullable=False, default="pending")
    ops_manager_id = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    ops_manager_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<VacationRequest(id={self.request_id}, user={self.user_name}, status={self.status})>"
