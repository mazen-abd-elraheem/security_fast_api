"""
SecureTrack Platform — Shared API Dependencies
Authentication dependencies and role checkers used across all routers.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_token
from app.core.exceptions import (
    NotFoundException,
    DuplicateException,
    ForbiddenException,
    BadRequestException,
    UnauthorizedException,
    GeofenceViolationException,
    DeviceNotTrustedException,
    OfflineSyncExpiredException,
    SecureTrackException,
)
from app.models.user import User
from app.enums import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency — extracts current user from JWT token."""
    payload = verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    return user


def require_role(*roles: UserRole):
    """Dependency factory — restricts endpoint to specific roles."""
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
        allowed = [r.value for r in roles]
        if user_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(allowed)}",
            )
        return current_user
    return role_checker


def handle_service_exception(e: SecureTrackException):
    """Convert domain exceptions to HTTP responses."""
    if isinstance(e, GeofenceViolationException):
        raise HTTPException(status_code=403, detail=e.message)
    elif isinstance(e, DeviceNotTrustedException):
        raise HTTPException(status_code=403, detail=e.message)
    elif isinstance(e, OfflineSyncExpiredException):
        raise HTTPException(status_code=410, detail=e.message)
    elif isinstance(e, NotFoundException):
        raise HTTPException(status_code=404, detail=e.message)
    elif isinstance(e, DuplicateException):
        raise HTTPException(status_code=409, detail=e.message)
    elif isinstance(e, ForbiddenException):
        raise HTTPException(status_code=403, detail=e.message)
    elif isinstance(e, BadRequestException):
        raise HTTPException(status_code=400, detail=e.message)
    elif isinstance(e, UnauthorizedException):
        raise HTTPException(status_code=401, detail=e.message)
    else:
        raise HTTPException(status_code=500, detail=e.message)
