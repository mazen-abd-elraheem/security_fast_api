"""
SecureTrack Platform — Password Reset Routes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.services.user_service import UserService

router = APIRouter()


@router.post("/reset-request", summary="Request password reset")
def request_password_reset(email: str, db: Session = Depends(get_db)):
    """
    Request a password reset link.
    In production, this would send an email with a reset token.
    For now, it validates the email exists.
    """
    user = UserService.get_by_email(db, email)
    # Always return success to prevent email enumeration
    return {"detail": "If the email exists, a reset link has been sent."}


@router.post("/reset-confirm", summary="Confirm password reset")
def confirm_password_reset(
    email: str,
    new_password: str,
    db: Session = Depends(get_db),
):
    """
    Confirm password reset with a new password.
    In production, this would require a valid reset token.
    """
    user = UserService.get_by_email(db, email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(new_password)
    db.commit()
    return {"detail": "Password reset successfully"}
