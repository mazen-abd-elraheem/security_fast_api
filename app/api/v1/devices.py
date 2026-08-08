"""
SecureTrack Platform — Device Routes
Device registration and management.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.device import DeviceRegisterRequest, DeviceResponse, DeviceListResponse
from app.services.device_service import DeviceService
from app.core.exceptions import SecureTrackException

router = APIRouter()


@router.post("/register", response_model=DeviceResponse, status_code=201, summary="Register device")
def register_device(
    device_data: DeviceRegisterRequest,
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Register a trusted device for the supervisor."""
    try:
        return DeviceService.register_device(db, current_user.user_id, device_data)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/my", response_model=DeviceListResponse, summary="My devices")
def get_my_devices(
    current_user: User = Depends(require_role(UserRole.SUPERVISOR)),
    db: Session = Depends(get_db),
):
    """Get all registered devices for the current user."""
    devices = DeviceService.get_user_devices(db, current_user.user_id)
    return DeviceListResponse(
        devices=[DeviceResponse.model_validate(d) for d in devices],
        total=len(devices),
    )


@router.delete("/{registry_id}", status_code=200, summary="Remove device")
def remove_device(
    registry_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a device registration."""
    try:
        DeviceService.remove_device(db, registry_id)
        return {"detail": "Device removed"}
    except SecureTrackException as e:
        handle_service_exception(e)
