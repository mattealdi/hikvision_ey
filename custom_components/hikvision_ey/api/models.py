"""Dataclasses per i modelli dati dell'integrazione Hikvision EY."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DeviceInfo:
    """Information about a Hikvision device from the cloud."""

    full_serial: str
    """Full serial number (used as unique ID in HA device registry)."""
    serial: str
    """Short serial number (used in API calls)."""
    name: str
    """Human-readable device name."""
    device_type: str
    """Device model/type string."""
    firmware_version: str
    """Current firmware version string."""
    is_online: bool | None
    """Effective online status used by entities (cloud + overlay/staleness)."""
    local_ip: str | None
    """LAN IP reported by cloud (may be stale if offline)."""
    wan_ip: str | None
    """WAN IP reported by cloud (may be stale if offline)."""
    wifi_signal: int | None
    """WiFi signal strength in dBm (None if wired or offline)."""
    update_available: bool | None
    """True if a firmware update is available."""
    locks: dict[int, int]
    """Channel → lock count mapping."""
    cameras: list[CameraInfo] = field(default_factory=list)
    """Cameras associated with this device."""
    raw: dict[str, Any] = field(default_factory=dict)
    """Raw API response (for diagnostics)."""
    cloud_is_online: bool | None = None
    """Raw online flag from cloud globalStatus (may be stale)."""
    online_source: str = "cloud"
    """Origin of is_online: cloud | call_status | call_status_error | stale."""


@dataclass
class CameraInfo:
    """Information about a camera channel on a device."""

    camera_id: str
    """Unique camera ID."""
    name: str
    """Human-readable camera name."""
    channel_number: int
    """Channel number on the device."""
    signal_status: int
    """Signal status code."""
    is_shown: bool
    """Whether this camera is shown in the app."""


@dataclass
class CallStatus:
    """Current call status for a device."""

    status: str
    """Status string: idle / ringing / in_call / unknown."""
    building_number: str | None = None
    floor_number: str | None = None
    zone_number: str | None = None
    unit_number: str | None = None
    device_number: str | None = None
    device_type: str | None = None
    lock_number: str | None = None

    @property
    def is_ringing(self) -> bool:
        """Return True if doorbell is currently ringing."""
        return self.status == "ringing"

    @property
    def is_in_call(self) -> bool:
        """Return True if a call is currently in progress."""
        return self.status == "in_call"


@dataclass
class UnlockResult:
    """Result of an unlock attempt."""

    success: bool
    """True if unlock was confirmed successful."""
    strategy: str
    """Strategy that was used."""
    meta_code: int | None = None
    """meta.code from API response (None for local ISAPI)."""
    http_status: int | None = None
    """HTTP status code."""
    error: str | None = None
    """Error message if success=False."""
    timestamp: datetime = field(default_factory=datetime.now)
    """When the unlock attempt was made."""
