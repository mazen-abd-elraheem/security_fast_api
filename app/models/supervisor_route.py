"""
SecureTrack Platform — Supervisor Route Model
Daily assignment dictating which sites a supervisor must visit.
"""
from sqlalchemy import Column, String, Date, Integer, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class SupervisorRoute(Base):
    __tablename__ = "supervisor_routes"
    __table_args__ = (
        Index('ix_routes_supervisor_date', 'supervisor_id', 'assigned_date'),
    )

    route_id = Column(String(36), primary_key=True, index=True)
    supervisor_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False, index=True)

    # The date this route assignment is for
    assigned_date = Column(Date, nullable=False, index=True)

    # Visit order in the day's itinerary (1, 2, 3, ...)
    visit_order = Column(Integer, nullable=False, default=1)

    # Status: pending, in_progress, completed, skipped
    status = Column(String(20), nullable=False, default="pending")

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    supervisor = relationship("User", back_populates="supervisor_routes", foreign_keys=[supervisor_id])
    site = relationship("Site", back_populates="supervisor_routes")
    visits = relationship("SupervisorVisit", back_populates="route")

    def __repr__(self):
        return f"<SupervisorRoute(route_id={self.route_id}, supervisor={self.supervisor_id}, site={self.site_id})>"
