"""
SecureTrack Platform — Daily Logbook Model
Digital version of the traditional "دفتر الأحوال".
Leader writes daily entries → Supervisor reviews aggregated → Ops Manager views all.
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index, Date
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.core.database import Base


class DailyLogbook(Base):
    __tablename__ = "daily_logbooks"
    __table_args__ = (
        Index('ix_logbook_site', 'site_id'),
        Index('ix_logbook_leader', 'leader_id'),
        Index('ix_logbook_date', 'date'),
    )

    logbook_id = Column(String(36), primary_key=True, index=True)

    # Which site and who wrote it
    site_id = Column(String(36), ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False)
    site_name = Column(String(255), nullable=False)  # Denormalized
    leader_id = Column(String(36), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    leader_name = Column(String(255), nullable=False)  # Denormalized

    # Logbook entry
    date = Column(Date, nullable=False)
    shift_label = Column(String(100), nullable=True)  # e.g., "وردية صباحيه", "مسائيه"
    events_summary = Column(Text, nullable=False)  # Main body of the daily report
    incidents_count = Column(Integer, nullable=False, default=0)
    guards_present = Column(Integer, nullable=True)
    guards_absent = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)  # Additional observations

    # Attachments (JSON array of URLs)
    attachments = Column(Text, nullable=True)  # JSON: ["url1", "url2", ...]

    # Review by Supervisor
    reviewed_by_supervisor = Column(String(36), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    supervisor_notes = Column(Text, nullable=True)
    supervisor_reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    site = relationship("Site", foreign_keys=[site_id])
    leader = relationship("User", foreign_keys=[leader_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by_supervisor])

    def __repr__(self):
        return f"<DailyLogbook(id={self.logbook_id}, site={self.site_name}, date={self.date})>"
