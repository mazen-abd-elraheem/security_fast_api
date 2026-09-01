"""
SecureTrack Platform — Shared Enums
Single source of truth for all enum types used across models, schemas, and services.
"""
from enum import Enum


class UserRole(str, Enum):
    CEO = "ceo"
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    LEADER = "leader"
    GUARD = "guard"
    OUTDOOR = "outdoor"
    LADY = "lady"
    PERSONNEL_OFFICER = "personnel_officer"
    HR = "hr"
    ACCOUNTANT = "accountant"
    OPERATIONS_MANAGER = "operations_manager"


class CashAdvanceStatus(str, Enum):
    PENDING = "pending"
    # Ops Manager step (replaces old supervisor step)
    OPS_APPROVED = "ops_approved"
    OPS_REJECTED = "ops_rejected"
    # Admin step
    ADMIN_APPROVED = "admin_approved"
    ADMIN_REJECTED = "admin_rejected"
    ADMIN_MODIFIED = "admin_modified"
    # CEO final step
    CEO_APPROVED = "ceo_approved"
    CEO_REJECTED = "ceo_rejected"
    # Legacy (keep for backward compat)
    SUPERVISOR_APPROVED = "supervisor_approved"
    SUPERVISOR_REJECTED = "supervisor_rejected"


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


class UniformItemType(str, Enum):
    SHIRT = "shirt"
    PANTS = "pants"
    SHOES = "shoes"
    BELT = "belt"
    CAP = "cap"
    JACKET = "jacket"
    TIE = "tie"
    OTHER = "other"


class UniformStatus(str, Enum):
    ISSUED = "issued"
    RETURNED = "returned"
    LOST = "lost"
    DAMAGED = "damaged"
    NEEDS_CLEANING = "needs_cleaning"


class UniformCondition(str, Enum):
    NEW = "new"
    GOOD = "good"
    NEEDS_CLEANING = "needs_cleaning"
    DAMAGED = "damaged"
    MISSING = "missing"
    RETURNED_ON_TERMINATION = "returned_on_termination"


class DocumentType(str, Enum):
    MILITARY_SERVICE = "military_service"
    EDUCATIONAL_QUALIFICATION = "educational_qualification"
    EMPLOYMENT_CONTRACT = "employment_contract"
    CRIMINAL_RECORD = "criminal_record"
    NATIONAL_ID_FRONT = "national_id_front"
    NATIONAL_ID_BACK = "national_id_back"

