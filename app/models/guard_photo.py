"""
SecureTrack Platform — Guard Photo Model
Stores photos taken by guards (uniform checks, selfies).
"""
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class GuardPhoto(Base):
    __tablename__ = "guard_photos"
    __table_args__ = (
        Index('ix_photos_guard_date', 'guard_id', 'created_at'),
    )

    photo_id = Column(String(36), primary_key=True, index=True)
    guard_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    # Photo type: uniform_check, selfie, incident_evidence
    photo_type = Column(String(30), nullable=False, default="uniform_check")

    # File path on server (relative to uploads dir)
    file_path = Column(String(500), nullable=False)

    # Optional note
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id])

    def __repr__(self):
        return f"<GuardPhoto(photo_id={self.photo_id}, guard={self.guard_id}, type={self.photo_type})>"
