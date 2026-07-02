"""Test fixtures per l'integrazione Hikvision EY."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Dati fixture ──────────────────────────────────────────────────────────────

MOCK_USERNAME = "test@example.com"
MOCK_PASSWORD = "TestPassword123"
MOCK_REGION = "EU"
MOCK_SERIAL = "K12345678"
MOCK_FULL_SERIAL = "K1234567890123456"
MOCK_BASE_URL = "https://apiieu.hik-connect.com"

# JWT di esempio con exp lontano (anno 2099)
MOCK_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJ0ZXN0IiwiZXhwIjo0MDcwOTA4ODAwfQ."
    "signature"
)
MOCK_REFRESH_TOKEN = "refresh_token_mock_12345"


# ── Risposte API mock ─────────────────────────────────────────────────────────


def make_login_response(
    session_id: str = MOCK_JWT,
    rf_session_id: str = MOCK_REFRESH_TOKEN,
    meta_code: int = 200,
) -> dict:
    """Crea una risposta login simulata."""
    return {
        "meta": {"code": meta_code, "message": "OK"},
        "loginSession": {
            "sessionId": session_id,
            "rfSessionId": rf_session_id,
        },
    }


def make_login_error_response(code: int, message: str = "Error") -> dict:
    """Crea una risposta di errore login simulata."""
    return {"meta": {"code": code, "message": message}}


def make_region_redirect_response(api_domain: str = "apiieu.hik-connect.com") -> dict:
    """Crea una risposta redirect regione (meta.code=1100)."""
    return {
        "meta": {"code": 1100, "message": "Region Redirect"},
        "loginArea": {"apiDomain": api_domain},
    }


def make_device_pagelist_response(devices: list[dict] | None = None) -> dict:
    """Crea una risposta pagelist device simulata."""
    if devices is None:
        devices = [make_device_entry()]
    return {
        "deviceInfos": devices,
        "statusInfos": {MOCK_SERIAL: {"globalStatus": 1}},
        "connectionInfos": {MOCK_SERIAL: {"localIp": "192.168.1.100", "netIp": "1.2.3.4"}},
        "wifiInfos": {MOCK_SERIAL: {"address": "192.168.1.100", "signal": -65}},
        "page": {"hasNext": False, "total": len(devices)},
        "meta": {"code": 200},
    }


def make_device_entry(
    serial: str = MOCK_SERIAL,
    full_serial: str = MOCK_FULL_SERIAL,
    name: str = "Test Doorbell",
    device_type: str = "DS-KV7413EY-IME2",
    version: str = "V2.2.72build211021",
) -> dict:
    """Crea una entry device simulata."""
    return {
        "deviceSerial": serial,
        "fullSerial": full_serial,
        "name": name,
        "deviceType": device_type,
        "version": version,
    }


def make_cameras_response(serial: str = MOCK_SERIAL) -> dict:
    """Crea una risposta cameras info simulata."""
    return {
        "cameraInfos": [
            {
                "cameraId": f"{serial}-1",
                "cameraName": "Front Camera",
                "channelNo": 1,
                "isShow": True,
                "deviceChannelInfo": {"signalStatus": 1},
            }
        ],
        "meta": {"code": 200},
    }


def make_call_status_response(status_code: int = 1) -> dict:
    """Crea una risposta call status simulata (1=idle, 2=ringing, 3=in_call)."""
    return {
        "data": json.dumps({"callStatus": status_code, "callerInfo": {}}),
        "meta": {"code": 200},
    }


def make_unlock_response(meta_code: int = 200) -> dict:
    """Crea una risposta unlock simulata."""
    return {"meta": {"code": meta_code, "message": "OK"}}


def make_refresh_response(
    session_id: str = MOCK_JWT,
    refresh_id: str = MOCK_REFRESH_TOKEN,
) -> dict:
    """Crea una risposta refresh login simulata."""
    return {
        "sessionInfo": {
            "sessionId": session_id,
            "refreshSessionId": refresh_id,
        },
        "meta": {"code": 200},
    }


# ── Fixture pytest ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_login_response():
    """Fixture: risposta login di successo."""
    return make_login_response()


@pytest.fixture
def mock_device_data():
    """Fixture: lista device simulata."""
    return make_device_pagelist_response()


@pytest.fixture
def mock_cameras_data():
    """Fixture: cameras info simulate."""
    return make_cameras_response()


@pytest.fixture
def mock_api_client():
    """Fixture: mock HikvisionEyClient già autenticato."""
    from custom_components.hikvision_ey.api.client import HikvisionEyClient
    from custom_components.hikvision_ey.api.models import (
        CameraInfo,
        CallStatus,
        DeviceInfo,
    )
    import datetime

    client = MagicMock(spec=HikvisionEyClient)
    client.base_url = MOCK_BASE_URL
    client.login_valid_until = datetime.datetime.now() + datetime.timedelta(hours=5)
    client.needs_token_refresh = MagicMock(return_value=False)
    client.login = AsyncMock()
    client.refresh_login = AsyncMock()
    client.ensure_authenticated = AsyncMock()
    client.close = AsyncMock()

    device = DeviceInfo(
        full_serial=MOCK_FULL_SERIAL,
        serial=MOCK_SERIAL,
        name="Test Doorbell",
        device_type="DS-KV7413EY-IME2",
        firmware_version="V2.2.72build211021",
        is_online=True,
        local_ip="192.168.1.100",
        wan_ip="1.2.3.4",
        wifi_signal=-65,
        update_available=False,
        locks={1: 1},
        cameras=[
            CameraInfo(
                camera_id=f"{MOCK_SERIAL}-1",
                name="Front Camera",
                channel_number=1,
                signal_status=1,
                is_shown=True,
            )
        ],
    )

    client.get_devices = AsyncMock(return_value=[device])
    client.get_cameras = AsyncMock(return_value=device.cameras)
    client.get_call_status = AsyncMock(return_value=CallStatus(status="idle"))
    client.answer_call = AsyncMock()
    client.hangup_call = AsyncMock()
    client.cancel_call = AsyncMock()
    client.restart_device = AsyncMock()

    return client


@pytest.fixture
def mock_hass():
    """Fixture: mock HomeAssistant base."""
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.config_entries = MagicMock()
    hass.data = {}
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    return hass


@pytest.fixture
def mock_config_entry():
    """Fixture: mock ConfigEntry con dati di test."""
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {
        "username": MOCK_USERNAME,
        "password": MOCK_PASSWORD,
        "region": MOCK_REGION,
        "timeout": 15,
        "base_url": MOCK_BASE_URL,
    }
    entry.options = {}
    entry.domain = "hikvision_ey"
    entry.version = 1
    entry.add_update_listener = MagicMock(return_value=lambda: None)
    return entry
