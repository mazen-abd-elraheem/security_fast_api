"""
SecureTrack Platform — Guard Document Model
Stores document photos uploaded by Personnel Officer for each guard.
Documents are stored permanently.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class GuardDocument(Base):
    __tablename__ = "guard_documents"

    document_id = Column(String(36), primary_key=True, index=True)

    # Which guard this document belongs to
    guard_id = Column(String(36), ForeignKey("users.user_id"), nullable=False, index=True)

    # Document details
    document_type = Column(String(50), nullable=False)  # military_service, educational_qualification, etc.
    file_url = Column(String(500), nullable=False)       # Path to uploaded image
    file_name = Column(String(255), nullable=True)       # Original filename
    notes = Column(String(500), nullable=True)

    # Who uploaded this document
    uploaded_by = Column(String(36), ForeignKey("users.user_id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guard = relationship("User", foreign_keys=[guard_id], backref="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by])

    def __repr__(self):
        return f"<GuardDocument(id={self.document_id}, guard={self.guard_id}, type={self.document_type})>"
