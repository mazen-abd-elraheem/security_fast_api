"""
SecureTrack Platform â€” User Routes
Profile management, location updates, and user listing.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
import calendar
from datetime import datetime

from app.core.database import get_db
from app.api.deps import get_current_user, require_role, handle_service_exception
from app.models.user import User
from app.enums import UserRole
from app.schemas.user import (
    UserResponse, UserUpdate, UserLocationUpdate, UserListResponse,
    AdminUserCreate, AdminUserUpdate,
)
from app.services.user_service import UserService
from app.core.exceptions import SecureTrackException
from app.core.audit import log_create, log_update, log_delete, log_read, snapshot

router = APIRouter()


# ==========================================
# Self-service endpoints
# ==========================================

@router.get("/me", response_model=UserResponse, summary="Get my profile")
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Get the authenticated user's profile."""
    return current_user


@router.put("/me", response_model=UserResponse, summary="Update my profile")
def update_my_profile(
    update_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the authenticated user's profile."""
    try:
        return UserService.update_profile(db, current_user.user_id, update_data)
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/me/location", response_model=UserResponse, summary="Update my GPS location")
def update_my_location(
    location: UserLocationUpdate,
    current_user: User = Depends(require_role(
        UserRole.SUPERVISOR, UserRole.GUARD, UserRole.OUTDOOR, UserRole.OPERATIONS_MANAGER, UserRole.LEADER, UserRole.ADMIN
    )),
    db: Session = Depends(get_db),
):
    """Update GPS location and auto-check-in for guards/outdoor if within geofence."""
    try:
        result = UserService.update_location(db, current_user.user_id, location)

        # Auto-check-in for guard/outdoor roles
        user_role = current_user.role.value if hasattr(current_user.role, 'value') else current_user.role
        if user_role in ('guard', 'outdoor'):
            _try_auto_checkin(db, current_user, location.latitude, location.longitude)

        return result
    except SecureTrackException as e:
        handle_service_exception(e)


def _try_auto_checkin(db: Session, user: User, lat: float, lng: float):
    """Silently attempt auto-checkin when guard/outdoor location is updated."""
    import uuid
    import logging
    from datetime import date, datetime, timezone
    from math import radians, cos, sin, asin, sqrt
    from app.models.guard_roster import GuardRoster
    from app.models.shift import Shift
    from app.models.site import Site
    from app.models.attendance_log import AttendanceLog
    from app.models.gps_tracking_ping import GpsTrackingPing

    logger = logging.getLogger("securetrack.auto_checkin")

    try:
        today = date.today()

        # Find today's roster (exclude canceled assignments)
        roster = (
            db.query(GuardRoster)
            .filter(GuardRoster.guard_id == user.user_id)
            .filter(GuardRoster.assigned_date == today)
            .filter(GuardRoster.status != "canceled")
            .first()
        )
        if not roster:
            return

        # Get site
        shift = db.query(Shift).filter(Shift.shift_id == roster.shift_id).first()
        if not shift:
            return
        site = db.query(Site).filter(Site.site_id == shift.site_id).first()
        if not site:
            return

        # Haversine distance
        lat1, lon1, lat2, lon2 = map(radians, [lat, lng, site.latitude, site.longitude])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        distance = 2 * asin(sqrt(a)) * 6371000

        now_utc = datetime.now(timezone.utc)

        # Find existing attendance log for this roster
        existing = db.query(AttendanceLog).filter(
            AttendanceLog.roster_id == roster.roster_id
        ).order_by(AttendanceLog.recorded_at.desc()).first()

        if distance > site.radius_meters:
            # OUTSIDE geofence â€” only checkout after 5+ consecutive outside pings
            if existing and not existing.checkout_at:
                recent_pings = (
                    db.query(GpsTrackingPing)
                    .filter(
                        GpsTrackingPing.user_id == user.user_id,
                        GpsTrackingPing.roster_id == roster.roster_id,
                    )
                    .order_by(GpsTrackingPing.recorded_at.desc())
                    .limit(5)
                    .all()
                )
                if len(recent_pings) >= 5 and all(not p.is_within_geofence for p in recent_pings):
                    existing.checkout_at = now_utc
                    existing.notes = (existing.notes or "") + " | Auto check-out (left geofence 5+ min)"
                    db.commit()
                    logger.info(f"[AUTO-CHECKOUT] {user.name} left {site.name} ({int(distance)}m) after 5+ outside pings")
            return

        # INSIDE geofence
        if existing and not existing.checkout_at:
            # Already checked in, still inside. Session stays open.
            pass
        elif existing and existing.checkout_at:
            # RE-ENTRY: guard returned to geofence after checkout.
            # Reopen the SAME session â€” accumulate outside time.
            outside_gap = (now_utc - existing.checkout_at).total_seconds()
            if outside_gap > 0:
                existing.total_outside_seconds = (existing.total_outside_seconds or 0) + outside_gap
            existing.checkout_at = None  # reopen the session
            existing.notes = (existing.notes or "") + f" | Re-entered geofence (was outside {int(outside_gap)}s)"
            db.commit()
            logger.info(f"[RE-ENTRY] {user.name} re-entered {site.name} ({int(distance)}m), was outside {int(outside_gap)}s")
        else:
            # First time checking in today
            log = AttendanceLog(
                log_id=str(uuid.uuid4()),
                roster_id=roster.roster_id,
                visit_id=None,
                supervisor_id=user.user_id,
                status="present",
                notes=f"Auto check-in at {int(distance)}m from site center",
                recorded_at=now_utc,
                total_outside_seconds=0.0,
            )
            db.add(log)
            db.commit()
            logger.info(f"[AUTO-CHECKIN] {user.name} checked in to {site.name} ({int(distance)}m)")
            
    except Exception as e:
        logger.error(f"[AUTO-CHECKIN] Failed for {user.user_id}: {e}")
        db.rollback()


# ==========================================
# Admin endpoints
# ==========================================

@router.get("", response_model=UserListResponse, summary="List users")
def list_users(
    role: Optional[str] = Query(None, description="Filter by role"),
    region: Optional[str] = Query(None, description="Filter by region"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    onboarding_status: Optional[str] = Query(None, description="Filter by onboarding status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=2000),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """List users with optional filtering. Admin only."""
    result = UserService.list_users(db, role=role, region=region, is_active=is_active, onboarding_status=onboarding_status, skip=skip, limit=limit)
    if current_user.role == UserRole.HR:
        restricted = [UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO]
        filtered = [u for u in result["users"] if u.role not in restricted]
        result["users"] = filtered
        result["total"] = len(filtered)
    return result


@router.post("", response_model=UserResponse, status_code=201, summary="Admin or HR creates a user")
def admin_create_user(
    user_data: AdminUserCreate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Admin creates any type of user account. HR creates restricted roles."""
    if current_user.role == UserRole.HR:
        allowed = [UserRole.GUARD, UserRole.OUTDOOR, UserRole.SUPERVISOR, UserRole.LEADER, UserRole.PERSONNEL_OFFICER, UserRole.LADY, UserRole.OPERATIONS_MANAGER]
        if user_data.role not in allowed:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="HR is not authorized to create a user with this role.")
            
    try:
        user = UserService.admin_create_user(db, user_data)
        log_create(db, current_user, "user", user)
        db.commit()
        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
def get_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Get a user's profile by ID. Admin and HR only."""
    try:
        user = UserService.get_by_id(db, user_id)
        if not user:
            from app.core.exceptions import NotFoundException
            raise NotFoundException("User", user_id)
        
        if current_user.role == UserRole.HR:
            restricted = [UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO]
            if user.role in restricted:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="HR is not authorized to view this user.")
                
        log_read(db, current_user, "user", user_id, user.name)
        db.commit()
        return user
    except SecureTrackException as e:
        handle_service_exception(e)


@router.put("/{user_id}", response_model=UserResponse, summary="Admin updates a user")
def admin_update_user(
    user_id: str,
    update_data: AdminUserUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.ACCOUNTANT, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Admin-level user update — can change any field. HR and Accountant have restrictions."""
    from fastapi import HTTPException as _HTTPException

    # ── ACCOUNTANT: payroll-only access, cannot change roles or sensitive fields ──
    if current_user.role == UserRole.ACCOUNTANT:
        if update_data.role is not None:
            raise _HTTPException(status_code=403, detail="Accountants are not authorized to change user roles.")
        # Accountants can only update salary-related fields; block everything else
        allowed_fields = {'payroll_amount', 'base_salary', 'daily_rate', 'bank_account', 'transfer_method', 'transfer_name'}
        provided = {k for k, v in update_data.model_dump(exclude_unset=True).items() if v is not None}
        disallowed = provided - allowed_fields
        if disallowed:
            raise _HTTPException(
                status_code=403,
                detail=f"Accountants can only update payroll/bank fields. Blocked fields: {', '.join(disallowed)}",
            )

    # ── HR: restricted roles only ──
    if current_user.role == UserRole.HR and update_data.role:
        allowed = [UserRole.GUARD, UserRole.OUTDOOR, UserRole.SUPERVISOR, UserRole.LEADER, UserRole.PERSONNEL_OFFICER, UserRole.LADY, UserRole.OPERATIONS_MANAGER]
        if update_data.role not in allowed:
            raise _HTTPException(status_code=403, detail="HR is not authorized to assign this role.")

    try:
        user = UserService.get_by_id(db, user_id)

        if current_user.role == UserRole.HR and user:
            restricted = [UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO]
            if user.role in restricted:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="HR is not authorized to update this user.")

        old = snapshot(user) if user else {}
        updated = UserService.admin_update_user(db, user_id, update_data)
        log_update(db, current_user, "user", old, updated)
        db.commit()
        return updated
    except SecureTrackException as e:
        handle_service_exception(e)



@router.post("/import-bulk", summary="Bulk import or update users (Excel)")
def bulk_import_users(
    rows: list[dict],
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """
    Import/Update users from Excel.
    - Uses badge_number as the unique identity key (same as export columns).
    - Mirrors admin_create_user / admin_update_user logic:
        * payroll_amount (= base salary) -> recalculates base_salary AND daily_rate = payroll / days_in_month
        * New users get a generated password: SecureTrack@<badge_number>
        * hire_date parsed from ISO string; falls back to now() if empty.
    """
    from app.api.v1.snapshots import create_import_snapshot
    import uuid, random
    from app.core.security import hash_password
    from app.enums import UserStatus

    now = datetime.now()
    days_in_month = float(calendar.monthrange(now.year, now.month)[1])

    before_rows = []
    after_rows = []

    HR_RESTRICTED_ROLES = {UserRole.ADMIN, UserRole.HR, UserRole.ACCOUNTANT, UserRole.CEO}
    HR_ALLOWED_ROLES = {
        UserRole.GUARD, UserRole.OUTDOOR, UserRole.SUPERVISOR, UserRole.LEADER,
        UserRole.PERSONNEL_OFFICER, UserRole.LADY, UserRole.OPERATIONS_MANAGER,
    }

    def _safe_float(v, default=0.0):
        try:
            return float(str(v).strip()) if str(v).strip() else default
        except (ValueError, TypeError):
            return default

    def _safe_str(v):
        s = str(v).strip() if v is not None else ""
        return s if s.lower() not in ("none", "null") else ""

    def _parse_hire_date(v):
        s = _safe_str(v)
        if not s:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                continue
        return None

    for row in rows:
        badge = _safe_str(row.get("badge_number", ""))
        if not badge:
            continue

        existing_user = db.query(User).filter(User.badge_number == badge).first()

        # HR cannot touch admin/hr/accountant/ceo accounts
        if current_user.role == UserRole.HR:
            if existing_user and existing_user.role in HR_RESTRICTED_ROLES:
                continue

        # Parse fields (matching export column keys exactly)
        name            = _safe_str(row.get("name", ""))
        email           = _safe_str(row.get("email", ""))
        role_str        = _safe_str(row.get("role", "guard")).lower()
        classification  = _safe_str(row.get("classification", ""))
        bank_account    = _safe_str(row.get("bank_account", ""))
        transfer_name   = _safe_str(row.get("transfer_name", ""))
        transfer_method = _safe_str(row.get("transfer_method", ""))
        status_str      = _safe_str(row.get("status", "active")).lower()
        insurance_str   = _safe_str(row.get("insurance_status", "none")).lower()
        hire_date       = _parse_hire_date(row.get("hire_date", ""))

        # Resolve salary: parse both columns separately and take the largest non-zero value.
        # "payroll_amount" in the export IS the base salary. base_salary is also exported.
        # We must _safe_float each individually (can't use `or` on strings — "0.0" is truthy).
        _pa = _safe_float(row.get("payroll_amount", 0))
        _bs = _safe_float(row.get("base_salary", 0))
        payroll_amount = max(_pa, _bs)   # take whichever is set
        base_salary    = payroll_amount
        daily_rate     = (payroll_amount / days_in_month) if payroll_amount > 0 else 0.0

        # Coerce role
        try:
            role = UserRole(role_str)
        except ValueError:
            role = UserRole.GUARD
        if current_user.role == UserRole.HR and role not in HR_ALLOWED_ROLES:
            role = UserRole.GUARD

        if existing_user:
            # -- SMART UPDATE: only touch fields that actually changed --
            before_dict = {c.name: getattr(existing_user, c.name) for c in existing_user.__table__.columns}

            changed = False

            def _str_changed(new_val: str, existing_val) -> bool:
                """Return True if new_val is non-empty and differs from existing."""
                if not new_val:
                    return False
                return str(existing_val or '').strip() != new_val.strip()

            if _str_changed(name, existing_user.name):
                existing_user.name = name; changed = True
            if _str_changed(email, existing_user.email):
                existing_user.email = email; changed = True
            if _str_changed(classification, existing_user.classification):
                existing_user.classification = classification; changed = True
            if _str_changed(bank_account, existing_user.bank_account):
                existing_user.bank_account = bank_account; changed = True
            if _str_changed(transfer_name, existing_user.transfer_name):
                existing_user.transfer_name = transfer_name; changed = True
            if _str_changed(transfer_method, existing_user.transfer_method):
                existing_user.transfer_method = transfer_method; changed = True
            if hire_date and existing_user.hire_date != hire_date:
                existing_user.hire_date = hire_date; changed = True
            if _str_changed(status_str, existing_user.status):
                existing_user.status = status_str; changed = True
            if _str_changed(insurance_str, existing_user.insurance_status):
                existing_user.insurance_status = insurance_str; changed = True
            if current_user.role == UserRole.ADMIN:
                if role.value != (existing_user.role or ''):
                    existing_user.role = role.value; changed = True
            if payroll_amount > 0 and abs(payroll_amount - (existing_user.base_salary or 0.0)) > 0.001:
                existing_user.payroll_amount = payroll_amount
                existing_user.base_salary    = base_salary
                existing_user.daily_rate     = daily_rate
                changed = True
            elif payroll_amount > 0 and (existing_user.daily_rate or 0.0) < 0.001:
                # salary unchanged but daily_rate was never computed — fix it
                existing_user.daily_rate = daily_rate
                changed = True

            if not changed:
                continue  # row is identical — skip to avoid unnecessary writes

            before_rows.append(before_dict)
            db.flush()
            after_dict = {c.name: getattr(existing_user, c.name) for c in existing_user.__table__.columns}
            after_rows.append(after_dict)

        else:
            # -- CREATE or UPDATE by email fallback --
            # Badge not found, but the email may already belong to another user
            # (e.g. badge number was edited in the export file). In that case,
            # update that user's badge_number + fields instead of creating a duplicate.
            if email:
                existing_user = db.query(User).filter(User.email == email).first()

            if existing_user:
                # Found by email — treat as UPDATE and also correct the badge_number
                before_dict = {c.name: getattr(existing_user, c.name) for c in existing_user.__table__.columns}
                before_rows.append(before_dict)

                existing_user.badge_number = badge   # adopt the new badge from file
                if name:            existing_user.name = name
                if classification:  existing_user.classification = classification
                if bank_account:    existing_user.bank_account = bank_account
                if transfer_name:   existing_user.transfer_name = transfer_name
                if transfer_method: existing_user.transfer_method = transfer_method
                if hire_date:       existing_user.hire_date = hire_date
                if status_str:      existing_user.status = status_str
                if insurance_str:   existing_user.insurance_status = insurance_str
                if current_user.role == UserRole.ADMIN:
                    existing_user.role = role.value
                if payroll_amount > 0:
                    existing_user.payroll_amount = payroll_amount
                    existing_user.base_salary    = base_salary
                    existing_user.daily_rate     = daily_rate

                db.flush()
                after_dict = {c.name: getattr(existing_user, c.name) for c in existing_user.__table__.columns}
                after_rows.append(after_dict)
                continue  # skip the create block below

            if not email:
                email = f"{badge}@securetrack.local"
            if not name:
                name = f"User {badge}"

            emp_code = str(random.randint(100000, 999999))
            while db.query(User).filter(User.employee_code == emp_code).first():
                emp_code = str(random.randint(100000, 999999))

            # Default password: SecureTrack@<badge>  (user should change on first login)
            default_password = f"SecureTrack@{badge}"

            new_user = User(
                user_id=str(uuid.uuid4()),
                badge_number=badge,
                employee_code=emp_code,
                name=name,
                email=email,
                password_hash=hash_password(default_password),
                role=role.value,
                classification=classification or None,
                bank_account=bank_account or None,
                transfer_name=transfer_name or None,
                transfer_method=transfer_method or None,
                payroll_amount=payroll_amount,
                base_salary=base_salary,
                daily_rate=daily_rate,
                hire_date=hire_date or datetime.now(),
                status=status_str or UserStatus.ACTIVE,
                insurance_status=insurance_str or "none",
                is_active=True,
            )
            db.add(new_user)
            db.flush()

            after_dict = {c.name: getattr(new_user, c.name) for c in new_user.__table__.columns}
            after_rows.append(after_dict)

    if after_rows:
        create_import_snapshot(
            db=db,
            table_name="users",
            before_rows=before_rows,
            after_rows=after_rows,
            user=current_user,
            description=f"Excel bulk import of {len(after_rows)} users",
        )
        db.commit()

    return {
        "detail": f"Imported/Updated {len(after_rows)} users",
        "updated_count": len(after_rows),
        "skipped_count": len(rows) - len(after_rows),
        "total_count": len(rows)
    }



@router.delete("/{user_id}", response_model=UserResponse, summary="Deactivate a user")
def deactivate_user(
    user_id: str,
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.HR)),
    db: Session = Depends(get_db),
):
    """Soft-delete: deactivate a user account."""
    try:
        user = UserService.get_by_id(db, user_id)
        log_delete(db, current_user, "user", user, f"Deactivated user: {user.name if user else user_id}")
        result = UserService.deactivate_user(db, user_id)
        db.commit()
        return result
    except SecureTrackException as e:
        handle_service_exception(e)


