"""Gerarchia eccezioni per l'integrazione Hikvision EY."""
from __future__ import annotations


class HikvisionEyError(Exception):
    """Base exception for all Hikvision EY errors."""


class AuthError(HikvisionEyError):
    """Authentication failed (wrong credentials or session expired)."""


class CaptchaRequired(HikvisionEyError):
    """Hik-Connect requires CAPTCHA — user must login via mobile app first."""


class DeviceOffline(HikvisionEyError):
    """Target device is offline or unreachable from cloud."""

    def __init__(self, serial: str = "") -> None:
        """Initialize with optional device serial."""
        self.serial = serial
        super().__init__(f"Device '{serial}' is offline" if serial else "Device is offline")


class RateLimited(HikvisionEyError):
    """API rate limit hit (HTTP 429)."""

    def __init__(self, retry_after: int | None = None) -> None:
        """Initialize with optional retry-after seconds."""
        self.retry_after = retry_after
        msg = "Rate limited by Hik-Connect API"
        if retry_after:
            msg += f" — retry after {retry_after}s"
        super().__init__(msg)


class UnlockFailed(HikvisionEyError):
    """All unlock strategies failed."""

    def __init__(self, strategy: str = "", meta_code: int | None = None) -> None:
        """Initialize with strategy name and optional meta code."""
        self.strategy = strategy
        self.meta_code = meta_code
        parts = [f"Unlock failed with strategy '{strategy}'"] if strategy else ["Unlock failed"]
        if meta_code is not None:
            parts.append(f"meta.code={meta_code}")
        super().__init__(" — ".join(parts))


class RegionRedirect(HikvisionEyError):
    """Cloud redirected to a different API domain (meta.code=1100)."""

    def __init__(self, new_domain: str) -> None:
        """Initialize with the new API domain."""
        self.new_domain = new_domain
        super().__init__(f"Region redirect to '{new_domain}'")


class LocalISAPIError(HikvisionEyError):
    """Error communicating with local ISAPI endpoint."""


class InvalidResponse(HikvisionEyError):
    """Unexpected or malformed API response."""
