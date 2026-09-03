"""
SecureTrack Platform — MFA (TOTP) Routes
Setup, verify, and manage Time-based One-Time Password (TOTP) for 2FA.
"""
import pyotp
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.core.security import create_access_token, create_refresh_token, verify_token
from app.core.audit import log_audit

router = APIRouter()


# ── Schemas ──

class TOTPSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    message: str


class TOTPVerifyRequest(BaseModel):
    code: str


class MFALoginRequest(BaseModel):
    user_id: str
    code: str


# ── Setup TOTP ──

@router.post("/setup", summary="Generate TOTP secret for the current user")
def setup_totp(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Generate a new TOTP secret for the current user.
    Returns the secret and a provisioning URI for QR code generation.
    The user must verify the code before TOTP is activated.
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="TOTP is already enabled. Disable it first to reconfigure.",
        )

    # Generate a new TOTP secret
    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    db.commit()

    # Generate provisioning URI for authenticator app
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="SecureTrack",
    )

    log_audit(
        db, current_user,
        action="totp_setup_initiated",
        target_type="user",
        target_id=current_user.user_id,
        target_name=current_user.name,
        description=f"TOTP setup initiated for {current_user.email}",
    )
    db.commit()

    return TOTPSetupResponse(
        secret=secret,
        provisioning_uri=uri,
        message="Scan the QR code with your authenticator app, then verify with a code.",
    )


# ── Verify & Activate TOTP ──

@router.post("/verify", summary="Verify TOTP code and activate 2FA")
def verify_totp(
    data: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Verify a TOTP code from the authenticator app.
    If valid, activates TOTP-based 2FA for the user.
    """
    if not current_user.totp_secret:
        raise HTTPException(
            status_code=400,
            detail="No TOTP secret found. Run /setup first.",
        )

    if current_user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="TOTP is already enabled.",
        )

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(data.code, valid_window=1):
        raise HTTPException(
            status_code=401,
            detail="Invalid TOTP code. Please try again.",
        )

    # Activate TOTP
    current_user.totp_enabled = True
    current_user.totp_confirmed_at = datetime.now(timezone.utc)
    db.commit()

    log_audit(
        db, current_user,
        action="totp_activated",
        target_type="user",
        target_id=current_user.user_id,
        target_name=current_user.name,
        description=f"TOTP 2FA activated for {current_user.email}",
        severity="warning",
    )
    db.commit()

    return {"message": "TOTP 2FA is now active. You will need a code on every login."}


# ── Complete MFA Login ──

@router.post("/login", summary="Complete MFA login with TOTP code")
def mfa_login(
    data: MFALoginRequest,
    db: Session = Depends(get_db),
):
    """
    Second step of MFA login: verify TOTP code and issue tokens.
    Called after /auth/login returns `mfa_required: true`.
    """
    user = db.query(User).filter(User.user_id == data.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    if not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status_code=400, detail="TOTP is not enabled for this user")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(data.code, valid_window=1):
        # Record failed MFA attempt
        user.failed_login_count = (user.failed_login_count or 0) + 1
        user.last_failed_login = datetime.now(timezone.utc)
        db.commit()

        log_audit(
            db, user,
            action="mfa_login_failed",
            target_type="user",
            target_id=user.user_id,
            target_name=user.name,
            description=f"Failed MFA login attempt for {user.email}",
            severity="warning",
        )
        db.commit()

        raise HTTPException(
            status_code=401,
            detail="Invalid TOTP code",
        )

    # Success — reset lockout and issue tokens
    user.failed_login_count = 0
    user.locked_until = None
    user.last_failed_login = None
    db.commit()

    token_data = {
        "sub": user.user_id,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    log_audit(
        db, user,
        action="mfa_login_success",
        target_type="user",
        target_id=user.user_id,
        target_name=user.name,
        description=f"MFA login completed for {user.email}",
    )
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "phone_number": user.phone_number,
        "role": user.role.value if hasattr(user.role, 'value') else user.role,
    }


# ── Disable TOTP ──

@router.delete("/disable", summary="Disable TOTP 2FA")
def disable_totp(
    data: TOTPVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Disable TOTP 2FA for the current user.
    Requires a valid TOTP code to confirm identity.
    """
    if not current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="TOTP is not enabled")

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(data.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid TOTP code")

    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.totp_confirmed_at = None
    db.commit()

    log_audit(
        db, current_user,
        action="totp_disabled",
        target_type="user",
        target_id=current_user.user_id,
        target_name=current_user.name,
        description=f"TOTP 2FA disabled for {current_user.email}",
        severity="warning",
    )
    db.commit()

    return {"message": "TOTP 2FA has been disabled."}


# ── Status ──

@router.get("/status", summary="Check MFA status")
def mfa_status(
    current_user: User = Depends(get_current_user),
):
    """Check whether TOTP 2FA is enabled for the current user."""
    return {
        "totp_enabled": bool(current_user.totp_enabled),
        "totp_confirmed_at": (
            current_user.totp_confirmed_at.isoformat()
            if current_user.totp_confirmed_at
            else None
        ),
    }
