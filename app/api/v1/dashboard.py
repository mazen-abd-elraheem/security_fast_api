"""
SecureTrack Platform — Dashboard Routes
Live status, coverage, and compliance data.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import date

from app.core.database import get_db
from app.api.deps import require_role
from app.models.user import User
from app.enums import UserRole
from app.schemas.dashboard import (
    LiveStatusResponse, CoverageResponse, ComplianceResponse, PlatformStatsResponse,
)
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/live", response_model=LiveStatusResponse, summary="Live site status")
def get_live_status(
    current_user: User = Depends(require_role(
        UserRole.ADMIN,
    )),
    db: Session = Depends(get_db),
):
    """Live overview of all sites: green/yellow/red status based on guard coverage."""
    return DashboardService.get_live_status(db)


@router.get("/supervisor/{supervisor_id}/progress", summary="Supervisor progress")
def get_supervisor_progress(
    supervisor_id: str,
    target_date: date = Query(default=None),
    current_user: User = Depends(require_role(
        UserRole.ADMIN,
    )),
    db: Session = Depends(get_db),
):
    """Get a supervisor's route progress for a date."""
    if not target_date:
        target_date = date.today()
    return DashboardService.get_supervisor_progress(db, supervisor_id, target_date)


@router.get("/stats", response_model=PlatformStatsResponse, summary="Platform statistics")
def get_platform_stats(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Overall platform statistics."""
    return DashboardService.get_platform_stats(db)
