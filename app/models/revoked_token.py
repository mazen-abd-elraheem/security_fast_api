"""
SecureTrack Platform — Revoked Token Model
Stores JTIs (JWT IDs) of revoked tokens to enable instant session invalidation.
"""
from sqlalchemy import Column, String, DateTime, Index
from datetime import datetime, timezone

from app.core.database import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    __table_args__ = (
        Index('ix_revoked_user', 'user_id'),
        Index('ix_revoked_expires', 'expires_at'),
    )

    jti = Column(String(36), primary_key=True)           # JWT ID — unique per token
    user_id = Column(String(36), nullable=False)
    revoked_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)         # Auto-cleanup after this time
    reason = Column(String(100), nullable=True)           # "logout", "deactivation", "password_change"

    def __repr__(self):
        return f"<RevokedToken(jti={self.jti}, user_id={self.user_id}, reason={self.reason})>"
