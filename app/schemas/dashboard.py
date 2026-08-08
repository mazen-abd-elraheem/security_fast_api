"""
SecureTrack Platform — Dashboard Schemas
"""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date


class SiteStatusItem(BaseModel):
    """Single site's live status."""
    site_id: str
    site_name: str
    region: Optional[str] = None
    required_guards: int = 0
    present_guards: int = 0
    coverage_percentage: float = 0.0
    status_color: str = "gray"  # green, yellow, red, gray
    last_supervisor_visit: Optional[datetime] = None
    has_active_incidents: bool = False


class LiveStatusResponse(BaseModel):
    """Live overview of all sites."""
    timestamp: datetime
    total_sites: int = 0
    sites_green: int = 0
    sites_yellow: int = 0
    sites_red: int = 0
    sites_gray: int = 0
    sites: List[SiteStatusItem] = []


class SupervisorProgressItem(BaseModel):
    """Single supervisor's route progress."""
    supervisor_id: str
    supervisor_name: str
    total_assigned: int = 0
    completed: int = 0
    in_progress: int = 0
    pending: int = 0
    skipped: int = 0
    progress_percentage: float = 0.0


class CoverageResponse(BaseModel):
    """Coverage summary for a date."""
    date: date
    total_scheduled_guards: int = 0
    total_present: int = 0
    total_absent: int = 0
    total_late: int = 0
    overall_attendance_rate: float = 0.0
    supervisors: List[SupervisorProgressItem] = []


class ComplianceResponse(BaseModel):
    """Overall compliance score report."""
    date_from: date
    date_to: date
    overall_score: float = 0.0
    total_visits_expected: int = 0
    total_visits_completed: int = 0
    visit_compliance_rate: float = 0.0
    total_attendance_records: int = 0
    attendance_rate: float = 0.0
    total_incidents: int = 0
    incidents_resolved: int = 0


class PlatformStatsResponse(BaseModel):
    """Overall platform statistics."""
    total_users: int = 0
    total_admins: int = 0
    total_supervisors: int = 0
    total_guards: int = 0
    total_sites: int = 0
    total_active_sites: int = 0
    total_shifts: int = 0
    total_visits_today: int = 0
    total_incidents_open: int = 0
