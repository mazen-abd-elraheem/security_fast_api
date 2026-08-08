"""
SecureTrack Platform — Configuration
Settings for the Security Field Force Management System.
"""
from pydantic_settings import BaseSettings
from functools import lru_cache
import logging
from typing import List

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from .env file"""

    APP_NAME: str = "SecureTrack"
    API_VERSION: str = "v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # SQLite Database for local dev
    DATABASE_URL: str = "sqlite:///./securetrack.db"

    # Security
    SECRET_KEY: str = "change-this-to-a-secure-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # File Uploads (photos, incident evidence)
    UPLOAD_DIR: str = "./uploaded_images"
    MAX_UPLOAD_SIZE_MB: int = 10
    MAX_CHECKIN_PHOTO_SIZE_MB: int = 5

    # Geofencing
    DEFAULT_GEOFENCE_RADIUS_METERS: int = 100

    # Offline Sync
    OFFLINE_SYNC_MAX_AGE_HOURS: int = 24

    # Device Fingerprinting
    MAX_TRUSTED_DEVICES_PER_USER: int = 3

    # Database Connection Pooling (production tuning for 10K+ users)
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40
    DATABASE_POOL_RECYCLE: int = 1800  # seconds

    # CORS — stored as a plain str so pydantic-settings never tries to JSON-parse it.
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse ALLOWED_ORIGINS into a list (comma-separated or single value)."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Server
    SERVER_HOST: str = "http://localhost:8000"

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    class Config:
        case_sensitive = True
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    logger.info(f"✓ Settings loaded for {settings.ENVIRONMENT} environment")
    return settings


settings = get_settings()
