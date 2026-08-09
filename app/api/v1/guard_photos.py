"""
SecureTrack Platform — Guard Photos Routes
Upload and retrieve guard selfie/uniform photos.
"""
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.models.guard_photo import GuardPhoto
from app.enums import UserRole

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads", "guard_photos")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("", status_code=201, summary="Upload guard photo")
async def upload_guard_photo(
    file: UploadFile = File(...),
    photo_type: str = Query("uniform_check", description="Type: uniform_check, selfie"),
    notes: Optional[str] = Query(None),
    current_user: User = Depends(require_role(UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """Guard uploads a selfie or uniform photo."""
    # Generate unique filename
    ext = os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg"
    filename = f"{current_user.user_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    # Save file
    contents = await file.read()
    with open(filepath, "wb") as f:
        f.write(contents)

    # Create DB record
    photo = GuardPhoto(
        photo_id=str(uuid.uuid4()),
        guard_id=current_user.user_id,
        photo_type=photo_type,
        file_path=filename,
        notes=notes,
        created_at=datetime.now(timezone.utc),
    )
    db.add(photo)
    db.commit()

    return {
        "photo_id": photo.photo_id,
        "guard_id": current_user.user_id,
        "photo_type": photo_type,
        "file_url": f"/api/v1/guard-photos/file/{filename}",
        "created_at": photo.created_at.isoformat(),
    }


@router.get("/my", summary="Get my photos")
def get_my_photos(
    current_user: User = Depends(require_role(UserRole.GUARD, UserRole.OUTDOOR)),
    db: Session = Depends(get_db),
):
    """Get all photos uploaded by the current guard."""
    photos = (
        db.query(GuardPhoto)
        .filter(GuardPhoto.guard_id == current_user.user_id)
        .order_by(GuardPhoto.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "photos": [
            {
                "photo_id": p.photo_id,
                "photo_type": p.photo_type,
                "file_url": f"/api/v1/guard-photos/file/{p.file_path}",
                "notes": p.notes,
                "created_at": p.created_at.isoformat(),
            }
            for p in photos
        ],
        "total": len(photos),
    }


@router.get("/guard/{guard_id}", summary="Get guard's photos (admin)")
def get_guard_photos(
    guard_id: str,
    current_user: User = Depends(require_role(
        UserRole.ADMIN, UserRole.SUPERVISOR,
    )),
    db: Session = Depends(get_db),
):
    """Admin/supervisor views a guard's uploaded photos."""
    guard = db.query(User).filter(User.user_id == guard_id).first()
    photos = (
        db.query(GuardPhoto)
        .filter(GuardPhoto.guard_id == guard_id)
        .order_by(GuardPhoto.created_at.desc())
        .limit(50)
        .all()
    )
    return {
        "guard_id": guard_id,
        "guard_name": guard.name if guard else "Unknown",
        "photos": [
            {
                "photo_id": p.photo_id,
                "photo_type": p.photo_type,
                "file_url": f"/api/v1/guard-photos/file/{p.file_path}",
                "notes": p.notes,
                "created_at": p.created_at.isoformat(),
            }
            for p in photos
        ],
        "total": len(photos),
    }


@router.get("/all", summary="Get all recent guard photos (admin)")
def get_all_guard_photos(
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(require_role(
        UserRole.ADMIN,
    )),
    db: Session = Depends(get_db),
):
    """Admin views all recent guard photos across all guards."""
    photos = (
        db.query(GuardPhoto)
        .order_by(GuardPhoto.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "photos": [
            {
                "photo_id": p.photo_id,
                "guard_id": p.guard_id,
                "guard_name": p.guard.name if p.guard else "Unknown",
                "photo_type": p.photo_type,
                "file_url": f"/api/v1/guard-photos/file/{p.file_path}",
                "notes": p.notes,
                "created_at": p.created_at.isoformat(),
            }
            for p in photos
        ],
        "total": len(photos),
    }


@router.get("/file/{filename}", summary="Serve photo file")
def serve_photo_file(filename: str):
    """Serve a guard photo file."""
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(filepath, media_type="image/jpeg")
