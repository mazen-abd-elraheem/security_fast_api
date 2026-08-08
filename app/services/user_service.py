"""
SecureTrack Platform — User Service
Handles registration, authentication, and profile management for all roles.
"""
import uuid
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User
from app.enums import UserRole, UserStatus
from app.schemas.user import UserCreate, UserUpdate, UserLocationUpdate, AdminUserCreate, AdminUserUpdate
from app.utils.email import send_email
from app.core.security import hash_password, verify_password
from app.core.exceptions import (
    NotFoundException,
    DuplicateException,
    BadRequestException,
)


class UserService:
    """Handles User Registration, Authentication, and Profile management."""

    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[User]:
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def get_by_id(db: Session, user_id: str) -> Optional[User]:
        return db.query(User).filter(User.user_id == user_id).first()

    @staticmethod
    def create_user(db: Session, user_in: UserCreate) -> User:
        """Register a new user."""
        if UserService.get_by_email(db, user_in.email):
            raise DuplicateException("Email already registered")

        if user_in.badge_number:
            existing = db.query(User).filter(User.badge_number == user_in.badge_number).first()
            if existing:
                raise DuplicateException(f"Badge number already in use: {user_in.badge_number}")

        db_user = User(
            user_id=str(uuid.uuid4()),
            name=user_in.name,
            email=user_in.email,
            phone_number=user_in.phone_number,
            password_hash=hash_password(user_in.password),
            role=user_in.role.value,  # initial role (could be guard)
            requested_role=user_in.role.value,
            badge_number=user_in.badge_number,
            region=user_in.region,
            latitude=user_in.latitude,
            longitude=user_in.longitude,
            is_active=False,  # Pending admin approval
            status=UserStatus.PENDING,
        )

        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def admin_create_user(db: Session, user_in: AdminUserCreate) -> User:
        """Admin creates any type of user account."""
        if UserService.get_by_email(db, user_in.email):
            raise DuplicateException("Email already registered")

        if user_in.badge_number:
            existing = db.query(User).filter(User.badge_number == user_in.badge_number).first()
            if existing:
                raise DuplicateException(f"Badge number already in use: {user_in.badge_number}")

        db_user = User(
            user_id=str(uuid.uuid4()),
            name=user_in.name,
            email=user_in.email,
            phone_number=user_in.phone_number,
            password_hash=hash_password(user_in.password),
            role=user_in.role.value,
            badge_number=user_in.badge_number,
            region=user_in.region,
            is_active=True,
            status=UserStatus.ACTIVE,
        )

        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user

    @staticmethod
    def authenticate(db: Session, email: str, password: str) -> Optional[User]:
        """Verify email and password."""
        user = UserService.get_by_email(db, email)
        if not user:
            return None
        if not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    @staticmethod
    def update_profile(db: Session, user_id: str, update_data: UserUpdate) -> User:
        """Update user profile fields."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        if update_data.name is not None:
            user.name = update_data.name
        if update_data.phone_number is not None:
            user.phone_number = update_data.phone_number
        if update_data.profile_image_url is not None:
            user.profile_image_url = update_data.profile_image_url
        if update_data.region is not None:
            user.region = update_data.region

        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def admin_update_user(db: Session, user_id: str, update_data: AdminUserUpdate) -> User:
        """Admin-level update — can change any field."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        if update_data.name is not None:
            user.name = update_data.name
        if update_data.phone_number is not None:
            user.phone_number = update_data.phone_number
        if update_data.email is not None:
            existing = UserService.get_by_email(db, update_data.email)
            if existing and existing.user_id != user_id:
                raise DuplicateException("Email already registered")
            user.email = update_data.email
        if update_data.role is not None:
            user.role = update_data.role.value
        if update_data.badge_number is not None:
            existing = db.query(User).filter(User.badge_number == update_data.badge_number, User.user_id != user_id).first()
            if existing:
                raise DuplicateException(f"Badge number already in use: {update_data.badge_number}")
            user.badge_number = update_data.badge_number
        if update_data.region is not None:
            user.region = update_data.region
        if update_data.new_password is not None:
            user.password_hash = hash_password(update_data.new_password)
        if update_data.is_active is not None:
            user.is_active = update_data.is_active

        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def update_location(db: Session, user_id: str, location: UserLocationUpdate) -> User:
        """Update user geolocation (from mobile GPS)."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        user.latitude = location.latitude
        user.longitude = location.longitude

        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def list_users(
        db: Session,
        role: Optional[str] = None,
        region: Optional[str] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """List users with optional filtering."""
        query = db.query(User)

        if role:
            query = query.filter(User.role == role)
        if region:
            query = query.filter(User.region == region)
        if is_active is not None:
            query = query.filter(User.is_active == is_active)

        total = query.count()
        users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()

        return {
            "users": users,
            "total": total,
            "page": (skip // limit) + 1,
            "page_size": limit,
            "total_pages": max(1, (total + limit - 1) // limit),
        }

    @staticmethod
    def deactivate_user(db: Session, user_id: str) -> User:
        """Soft-delete: deactivate a user account."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        user.is_active = False
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def list_pending_users(
        db: Session,
        skip: int = 0,
        limit: int = 20,
    ) -> dict:
        """List users pending admin approval (status=PENDING)."""
        query = db.query(User).filter(User.status == UserStatus.PENDING)

        total = query.count()
        users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()

        return {
            "users": users,
            "total": total,
            "page": (skip // limit) + 1,
            "page_size": limit,
            "total_pages": max(1, (total + limit - 1) // limit),
        }

    @staticmethod
    def activate_user(db: Session, user_id: str) -> User:
        """Admin activates a user — sets is_active=True and status=ACTIVE."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        if user.status == UserStatus.ACTIVE and user.is_active:
            raise BadRequestException("User is already active")

        user.is_active = True
        user.status = UserStatus.ACTIVE
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def approve_user(db: Session, user_id: str, role: Optional[str] = None) -> User:
        """Admin approves a pending user — sets status=ACTIVE, optionally changes role."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        if user.status != UserStatus.PENDING:
            raise BadRequestException(f"User is not pending approval (current status: {user.status})")

        # Set role — admin can override, otherwise use requested_role
        if role:
            user.role = role
        elif user.requested_role:
            user.role = user.requested_role

        user.status = UserStatus.ACTIVE
        user.is_active = True
        db.commit()
        db.refresh(user)

        # Send approval email
        try:
            send_email_notification = True
            from app.utils.email import send_approval_email
            send_approval_email(user.email, user.name, user.role)
        except Exception:
            pass  # Email failure should not block approval

        return user

    @staticmethod
    def reject_user(db: Session, user_id: str, reason: str = "") -> dict:
        """Admin rejects a pending registration — keeps user with REJECTED status."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        if user.status == UserStatus.ACTIVE and user.is_active:
            raise BadRequestException("Cannot reject an active user. Deactivate instead.")

        user.status = UserStatus.REJECTED
        user.is_active = False
        db.commit()
        db.refresh(user)

        # Send rejection email
        try:
            from app.utils.email import send_rejection_email
            send_rejection_email(user.email, user.name, reason)
        except Exception:
            pass  # Email failure should not block rejection

        return {
            "message": f"Registration for '{user.name}' ({user.email}) has been rejected.",
            "user_id": user_id,
            "status": "rejected",
        }

    @staticmethod
    def delete_user(db: Session, user_id: str) -> dict:
        """Permanently delete a user record."""
        user = UserService.get_by_id(db, user_id)
        if not user:
            raise NotFoundException("User", user_id)

        user_name = user.name
        user_email = user.email
        db.delete(user)
        db.commit()

        return {
            "message": f"User '{user_name}' ({user_email}) has been permanently deleted.",
            "user_id": user_id,
        }

    @staticmethod
    def get_activation_status(db: Session, email: str) -> dict:
        """Check activation/approval status for a registered user by email."""
        user = UserService.get_by_email(db, email)
        if not user:
            raise NotFoundException("User", email)

        status = user.status or "pending"
        if status == UserStatus.ACTIVE or status == "active":
            message = "Your account has been approved. You can now log in."
        elif status == UserStatus.REJECTED or status == "rejected":
            message = "Your account registration has been rejected. Please contact your administrator."
        else:
            message = "Your account is pending admin approval. Please wait for activation."

        return {
            "email": user.email,
            "name": user.name,
            "status": status if isinstance(status, str) else status.value,
            "message": message,
            "registered_at": user.created_at,
        }
