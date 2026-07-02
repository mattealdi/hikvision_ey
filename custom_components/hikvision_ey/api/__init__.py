"""Pacchetto API per l'integrazione Hikvision EY."""
from __future__ import annotations

from .client import HikvisionEyClient
from .exceptions import (
    AuthError,
    CaptchaRequired,
    DeviceOffline,
    HikvisionEyError,
    InvalidResponse,
    LocalISAPIError,
    RateLimited,
    RegionRedirect,
    UnlockFailed,
)
from .isapi import LocalISAPIClient
from .models import CallStatus, CameraInfo, DeviceInfo, UnlockResult
from .unlock_strategies import UnlockManager

__all__ = [
    "HikvisionEyClient",
    "LocalISAPIClient",
    "UnlockManager",
    # Exceptions
    "HikvisionEyError",
    "AuthError",
    "CaptchaRequired",
    "DeviceOffline",
    "RateLimited",
    "UnlockFailed",
    "RegionRedirect",
    "LocalISAPIError",
    "InvalidResponse",
    # Models
    "DeviceInfo",
    "CameraInfo",
    "CallStatus",
    "UnlockResult",
]
