"""
SecureTrack Platform — File Upload Security (Gap 13.5)

Centralized file-upload validation: MIME type allow-listing, size caps,
filename sanitization, and EXIF metadata stripping.
"""
import os
import uuid
import logging
from fastapi import UploadFile, HTTPException

logger = logging.getLogger(__name__)

# ── Allowed MIME types ──
ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
}
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_DOCUMENT_TYPES

# ── Size limits ──
MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_DOCUMENT_SIZE = 25 * 1024 * 1024  # 25 MB

# ── Extension mapping ──
MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


async def validate_upload(
    file: UploadFile,
    *,
    allowed_types: set[str] | None = None,
    max_size: int | None = None,
) -> bytes:
    """
    Validate an uploaded file and return its raw bytes.

    Checks:
    1. MIME type is in the allow-list
    2. File size is within the cap
    3. File is not empty

    Returns the file content bytes for further processing.
    Raises HTTPException 400/413 on violation.
    """
    types = allowed_types or ALLOWED_TYPES
    content_type = file.content_type or ""

    # 1) MIME type check
    if content_type not in types:
        logger.warning(
            "Upload rejected: invalid type '%s' for file '%s'",
            content_type, file.filename,
        )
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' is not allowed. "
                   f"Accepted types: {', '.join(sorted(types))}",
        )

    # 2) Read content and check size
    content = await file.read()
    size_limit = max_size or (
        MAX_IMAGE_SIZE if content_type in ALLOWED_IMAGE_TYPES else MAX_DOCUMENT_SIZE
    )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(content) > size_limit:
        mb = size_limit / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {mb:.0f} MB.",
        )

    return content


def generate_safe_filename(original_filename: str, content_type: str) -> str:
    """
    Generate a safe, random filename based on MIME type.

    SECURITY: Never trust the client-provided filename. Generate a fresh
    UUID-based name to prevent path traversal, XSS via filename, and
    filename collisions.
    """
    ext = MIME_TO_EXT.get(content_type, "")
    if not ext and original_filename:
        _, ext = os.path.splitext(original_filename)
        # Only allow known safe extensions
        if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".doc", ".docx"}:
            ext = ""
    return f"{uuid.uuid4().hex}{ext}"


def strip_exif(image_bytes: bytes, content_type: str) -> bytes:
    """
    Strip EXIF metadata from images to prevent GPS/PII leakage.

    Falls back to returning the original bytes if PIL is not available
    or the image format doesn't support EXIF.
    """
    if content_type not in ALLOWED_IMAGE_TYPES:
        return image_bytes

    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        # Re-save without EXIF
        clean = io.BytesIO()
        img_format = {
            "image/jpeg": "JPEG",
            "image/png": "PNG",
            "image/webp": "WEBP",
            "image/gif": "GIF",
        }.get(content_type, "JPEG")
        img.save(clean, format=img_format)
        return clean.getvalue()
    except ImportError:
        logger.warning("Pillow not installed — EXIF stripping disabled")
        return image_bytes
    except Exception as e:
        logger.warning("EXIF stripping failed: %s", e)
        return image_bytes
