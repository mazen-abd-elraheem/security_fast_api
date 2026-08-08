"""
SecureTrack Platform — Custom Domain Exceptions
Services raise these instead of HTTPException, keeping them HTTP-agnostic.
The API layer catches these and converts to proper HTTP responses.
"""


class SecureTrackException(Exception):
    """Base exception for all SecureTrack platform errors."""
    def __init__(self, message: str = "An error occurred"):
        self.message = message
        super().__init__(self.message)


class NotFoundException(SecureTrackException):
    """Resource not found (→ HTTP 404)."""
    def __init__(self, resource: str = "Resource", identifier: str = ""):
        msg = f"{resource} not found" + (f": {identifier}" if identifier else "")
        super().__init__(msg)


class DuplicateException(SecureTrackException):
    """Resource already exists (→ HTTP 409)."""
    def __init__(self, message: str = "Resource already exists"):
        super().__init__(message)


class ForbiddenException(SecureTrackException):
    """User lacks permission (→ HTTP 403)."""
    def __init__(self, message: str = "You do not have permission"):
        super().__init__(message)


class BadRequestException(SecureTrackException):
    """Invalid operation or data (→ HTTP 400)."""
    def __init__(self, message: str = "Bad request"):
        super().__init__(message)


class UnauthorizedException(SecureTrackException):
    """Authentication failed (→ HTTP 401)."""
    def __init__(self, message: str = "Not authenticated"):
        super().__init__(message)


class GeofenceViolationException(SecureTrackException):
    """Supervisor is outside the site's geofence radius (→ HTTP 403)."""
    def __init__(self, distance_meters: float, required_meters: float):
        msg = (
            f"Geofence violation: you are {distance_meters:.0f}m from the site, "
            f"but must be within {required_meters:.0f}m"
        )
        self.distance_meters = distance_meters
        self.required_meters = required_meters
        super().__init__(msg)


class DeviceNotTrustedException(SecureTrackException):
    """Login attempt from an unregistered/untrusted device (→ HTTP 403)."""
    def __init__(self, device_id: str = ""):
        msg = "Device is not trusted" + (f": {device_id}" if device_id else "")
        super().__init__(msg)


class OfflineSyncExpiredException(SecureTrackException):
    """Offline-cached data is too old to accept (→ HTTP 410)."""
    def __init__(self, max_age_hours: int = 24):
        msg = f"Offline data expired — must be synced within {max_age_hours} hours"
        super().__init__(msg)
