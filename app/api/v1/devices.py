"""
SecureTrack Platform — Device Routes
Device registration and management.
"""
from fastapi import APIRouter, Depends, HTTPException, status
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
    """Remove a device registration.

    SECURITY: Only the device owner (or an admin) can remove a device.
    Without this check any authenticated user could delete anyone else's
    trusted device registration (IDOR vulnerability).
    """
    try:
        # ── Ownership check ──
        from app.models.device_registry import DeviceRegistry
        from app.enums import UserRole as _Role
        device = db.query(DeviceRegistry).filter(
            DeviceRegistry.registry_id == registry_id
        ).first()
        if device:
            user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
            if device.user_id != current_user.user_id and user_role != _Role.ADMIN.value:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorized to remove this device.",
                )
        DeviceService.remove_device(db, registry_id)
        return {"detail": "Device removed"}
    except SecureTrackException as e:
        handle_service_exception(e)

