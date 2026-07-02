"""URL builders per gli endpoint Hik-Connect e ISAPI."""
from __future__ import annotations

from ..const import REGION_URLS


def base_url(region: str) -> str:
    """Return the base API URL for a given region name.

    Args:
        region: One of 'EU', 'Asia', 'USA'.

    Returns:
        HTTPS base URL string.
    """
    return REGION_URLS.get(region, REGION_URLS["EU"])


# ── Auth ──────────────────────────────────────────────────────────────────────


def login(base: str) -> str:
    """POST — login with username/password."""
    return f"{base}/v3/users/login/v2"


def refresh_login(base: str) -> str:
    """PUT — refresh session using refreshSessionId."""
    return f"{base}/v3/apigateway/login"


# ── Devices ───────────────────────────────────────────────────────────────────


def device_pagelist(base: str, limit: int = 50, offset: int = 0) -> str:
    """GET — paginated device list with all filter categories."""
    filters = "TIME_PLAN,CONNECTION,SWITCH,STATUS,STATUS_EXT,WIFI,NODISTURB,P2P,KMS,HIDDNS"
    return (
        f"{base}/v3/userdevices/v1/devices/pagelist"
        f"?groupId=-1&limit={limit}&offset={offset}&filter={filters}"
    )


def cameras_info(base: str, device_serial: str) -> str:
    """GET — camera info for a device."""
    return f"{base}/v3/userdevices/v1/cameras/info?deviceSerial={device_serial}"


# ── Call / Intercom ───────────────────────────────────────────────────────────


def call_status(base: str, serial: str) -> str:
    """GET — current call status for a device."""
    return f"{base}/v3/devconfig/v1/call/{serial}/status"


def call_operation(base: str, serial: str, cmd_id: int) -> str:
    """PUT — call operation (answer=2, cancel=3, hangup=5)."""
    return f"{base}/v3/devconfig/v1/call/{serial}/operation?cmdId={cmd_id}"


# ── Unlock strategies ─────────────────────────────────────────────────────────


def unlock_verified(base: str) -> str:
    """POST — endpoint ISAPI tunnel verificato empiricamente.

    Verificato via mitmproxy sull'app HikConnect iOS (luglio 2026)
    per DS-KV7413EY-IME2 + DS-KH7300EY-WTE2 + DS-KAD7063.

    Body form-urlencoded:
        apiData=PUT /ISAPI/AccessControl/RemoteControl/door/1
        apiKey=100044
        channelNo=1
        deviceSerial=<serial>
        method=0
    """
    return f"{base}/v3/userdevices/v1/isapi"


def unlock_a1(base: str, serial: str, channel: int, lock_index: int) -> str:
    """PUT — Strategy A1: legacy remote/unlock endpoint.

    Works on older IP-native devices; ineffective on EY series.
    """
    return (
        f"{base}/v3/devconfig/v1/call/{serial}/{channel}/remote/unlock"
        f"?srcId=1&lockId={lock_index}&userType=0"
    )


def unlock_a2(base: str, serial: str, lock_index: int) -> str:
    """PUT — Strategy A2: userdevices smart lock endpoint.

    Present in recent Hik-Connect APK for smart lock devices.
    """
    return f"{base}/v3/userdevices/v1/devices/{serial}/lock?lockId={lock_index}"


def unlock_a3(base: str, serial: str, relay_id: int) -> str:
    """POST — Strategy A3: relay control for 2-wire bus devices (EY series).

    Hypothesis: used for devices with relay outputs (KV/EY series on KAD7063 bus).
    """
    return f"{base}/v3/devconfig/v1/call/{serial}/relay?relayId={relay_id}&cmd=1"


def unlock_a3_alt(base: str, serial: str, channel: int, relay_id: int) -> str:
    """POST — Strategy A3 alternative format with srcId and channelNo."""
    return (
        f"{base}/v3/devconfig/v1/call/{serial}/relay"
        f"?srcId=1&channelNo={channel}&relayId={relay_id}&cmd=1"
    )


def unlock_a4_primary(base: str, serial: str) -> str:
    """POST — Strategy A4 primary: ISAPI over cloud tunnel."""
    return f"{base}/v3/devconfig/v1/isapi/{serial}"


def unlock_a4_fallback(base: str, serial: str) -> str:
    """POST — Strategy A4 fallback path (alternative tunnel endpoint)."""
    return f"{base}/v3/devconfig/v1/pass/{serial}/isapi"


# ── Device management ─────────────────────────────────────────────────────────


def device_restart(base: str, serial: str) -> str:
    """PUT — restart a device remotely."""
    return f"{base}/v3/devconfig/v1/device/{serial}/restart"


# ── Local ISAPI ───────────────────────────────────────────────────────────────


def isapi_door_control(host: str, door_index: int) -> str:
    """PUT — local ISAPI door/lock control (1-based door index).

    Args:
        host: IP or hostname of the monitor.
        door_index: 0-based lock index (converted to 1-based here).
    """
    return f"http://{host}/ISAPI/AccessControl/RemoteControl/door/{door_index + 1}"


def isapi_device_info(host: str) -> str:
    """GET — local ISAPI device info (used for connectivity test)."""
    return f"http://{host}/ISAPI/System/deviceInfo"
