"""
SecureTrack Platform — Shared Enums
Single source of truth for all enum types used across models, schemas, and services.
"""
from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    GUARD = "guard"
    OUTDOOR = "outdoor"


class SiteStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class UserStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"


class RosterStatus(str, Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CANCELED = "canceled"


class RouteStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class AttendanceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LATE = "late"
    REPLACEMENT = "replacement"


class IncidentCategory(str, Enum):
    EQUIPMENT_DAMAGE = "equipment_damage"
    SECURITY_BREACH = "security_breach"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    MISSING_GUARD = "missing_guard"
    PROPERTY_DAMAGE = "property_damage"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    OTHER = "other"


class IncidentSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"
