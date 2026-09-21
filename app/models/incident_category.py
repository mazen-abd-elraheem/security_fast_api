"""
SecureTrack Platform — Incident Category Model
Admin-managed dynamic categories for security incident reporting.
"""
import json
from sqlalchemy import Column, String, Text, Boolean, DateTime
from datetime import datetime, timezone

from app.core.database import Base


class IncidentCategory(Base):
    __tablename__ = "incident_categories"

    category_id = Column(String(36), primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    name_ar = Column(String(100), nullable=True)

    # Severity assigned to this category: low / medium / high / critical
    severity = Column(String(20), nullable=False, default="medium")

    # Message shown to the reporter after submitting an incident of this category
    corrective_action = Column(Text, nullable=True)

    # JSON-encoded list of role strings that receive alert notifications
    # e.g. '["admin", "supervisor"]'
    alert_roles_json = Column(Text, nullable=False, default="[]")

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    @property
    def alert_roles(self):
        """Return alert_roles as a Python list."""
        try:
            return json.loads(self.alert_roles_json or "[]")
        except Exception:
            return []

    @alert_roles.setter
    def alert_roles(self, value):
        self.alert_roles_json = json.dumps(value or [])

    def __repr__(self):
        return f"<IncidentCategory(id={self.category_id}, name={self.name}, severity={self.severity})>"
