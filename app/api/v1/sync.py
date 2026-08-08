"""
SecureTrack Platform — Sync Routes
Offline data push from mobile supervisors.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.sync import SyncPushRequest, SyncStatusResponse
from app.services.sync_service import SyncService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("/push", response_model=SyncStatusResponse, summary="Push offline data")
def push_offline_data(
    sync_data: SyncPushRequest,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """
    Push cached offline data to the server.

    Used when the supervisor was in a network dead zone (e.g., underground parking).
    Each record is processed independently — failures don't block others.
    Records older than OFFLINE_SYNC_MAX_AGE_HOURS are rejected.
    """
    try:
        return SyncService.push_offline_data(db, current_user.user_id, sync_data)
    except SecureTrackException as e:
        handle_service_exception(e)
