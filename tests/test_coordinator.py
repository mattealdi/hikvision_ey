"""Test per i coordinator (DeviceCoordinator e CallStatusCoordinator)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hikvision_ey.api.exceptions import (
    AuthError,
    CaptchaRequired,
    DeviceOffline,
    HikvisionEyError,
)
from custom_components.hikvision_ey.api.models import CallStatus, DeviceInfo
from custom_components.hikvision_ey.const import (
    DEFAULT_CALL_POLL_INTERVAL_IDLE,
    DEFAULT_CALL_POLL_INTERVAL_RINGING,
    DOMAIN,
    EVENT_CALL_ENDED,
    EVENT_CALL_STARTED,
    EVENT_CLOUD_RECONNECTED,
    EVENT_DOORBELL_PRESSED,
)
from custom_components.hikvision_ey.coordinator import (
    HikvisionEyCallStatusCoordinator,
    HikvisionEyDeviceCoordinator,
)
from tests.conftest import MOCK_PASSWORD, MOCK_SERIAL, MOCK_USERNAME


def make_mock_device(serial: str = MOCK_SERIAL, is_online: bool = True) -> DeviceInfo:
    """Crea un DeviceInfo mock per i test."""
    from custom_components.hikvision_ey.api.models import CameraInfo
    return DeviceInfo(
        full_serial=f"{serial}_FULL",
        serial=serial,
        name="Test Device",
        device_type="DS-KV7413EY-IME2",
        firmware_version="V2.2.72",
        is_online=is_online,
        local_ip="192.168.1.100" if is_online else None,
        wan_ip="1.2.3.4" if is_online else None,
        wifi_signal=-65 if is_online else None,
        update_available=False,
        locks={1: 1},
        cameras=[
            CameraInfo(
                camera_id=f"{serial}-1",
                name="Camera 1",
                channel_number=1,
                signal_status=1,
                is_shown=True,
            )
        ],
    )


# ── DeviceCoordinator ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_coordinator_first_update(mock_hass, mock_config_entry) -> None:
    """Primo aggiornamento deve effettuare il login e restituire i device."""
    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)

    mock_device = make_mock_device()
    coordinator.client.login = AsyncMock()
    coordinator.client.get_devices = AsyncMock(return_value=[mock_device])

    mock_hass.bus.async_fire = MagicMock()

    data = await coordinator._async_update_data()

    coordinator.client.login.assert_called_once_with(MOCK_USERNAME, MOCK_PASSWORD)
    assert len(data) == 1
    assert data[0].serial == MOCK_SERIAL
    assert coordinator._is_logged_in is True
    await coordinator.async_close()


@pytest.mark.asyncio
async def test_device_coordinator_token_refresh(mock_hass, mock_config_entry) -> None:
    """Se il token sta per scadere, deve chiamare ensure_authenticated."""
    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)
    coordinator._is_logged_in = True

    mock_device = make_mock_device()
    coordinator.client.ensure_authenticated = AsyncMock()
    coordinator.client.get_devices = AsyncMock(return_value=[mock_device])

    data = await coordinator._async_update_data()

    coordinator.client.ensure_authenticated.assert_called_once_with(MOCK_USERNAME, MOCK_PASSWORD)
    assert len(data) == 1
    await coordinator.async_close()


@pytest.mark.asyncio
async def test_device_coordinator_auth_error_raises(mock_hass, mock_config_entry) -> None:
    """AuthError deve sollevare ConfigEntryAuthFailed."""
    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)
    coordinator.client.login = AsyncMock(side_effect=AuthError("Bad credentials"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    await coordinator.async_close()


@pytest.mark.asyncio
async def test_device_coordinator_captcha_raises(mock_hass, mock_config_entry) -> None:
    """CaptchaRequired deve sollevare ConfigEntryAuthFailed."""
    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)
    coordinator.client.login = AsyncMock(side_effect=CaptchaRequired("CAPTCHA"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    await coordinator.async_close()


@pytest.mark.asyncio
async def test_device_coordinator_network_error_raises(mock_hass, mock_config_entry) -> None:
    """Errore di rete deve sollevare UpdateFailed."""
    import aiohttp

    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)
    coordinator.client.login = AsyncMock(side_effect=aiohttp.ClientError("Network error"))

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    await coordinator.async_close()


@pytest.mark.asyncio
async def test_device_coordinator_cloud_reconnected_event(mock_hass, mock_config_entry) -> None:
    """Al primo login deve emettere EVENT_CLOUD_RECONNECTED."""
    coordinator = HikvisionEyDeviceCoordinator(mock_hass, mock_config_entry)

    mock_device = make_mock_device()
    coordinator.client.login = AsyncMock()
    coordinator.client.get_devices = AsyncMock(return_value=[mock_device])
    mock_hass.bus.async_fire = MagicMock()

    await coordinator._async_update_data()

    mock_hass.bus.async_fire.assert_called_once_with(
        EVENT_CLOUD_RECONNECTED,
        {"domain": DOMAIN, "timestamp": mock_hass.bus.async_fire.call_args[0][1]["timestamp"]},
    )
    await coordinator.async_close()


# ── CallStatusCoordinator ─────────────────────────────────────────────────────


def make_call_coordinator(mock_hass, mock_device_coordinator):
    """Helper: crea un CallStatusCoordinator con device coordinator mock."""
    coordinator = HikvisionEyCallStatusCoordinator(mock_hass, mock_device_coordinator)
    return coordinator


@pytest.mark.asyncio
async def test_call_coordinator_no_devices(mock_hass, mock_config_entry) -> None:
    """Senza device online deve restituire dict vuoto."""
    device_coord = MagicMock()
    device_coord.data = []
    device_coord.client = MagicMock()

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    data = await call_coord._async_update_data()

    assert data == {}


@pytest.mark.asyncio
async def test_call_coordinator_idle_device(mock_hass, mock_config_entry) -> None:
    """Device idle deve restituire status idle senza eventi."""
    device = make_mock_device()
    device_coord = MagicMock()
    device_coord.data = [device]
    device_coord.client = MagicMock()
    device_coord.client.get_call_status = AsyncMock(return_value=CallStatus(status="idle"))

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    mock_hass.bus.async_fire = MagicMock()

    data = await call_coord._async_update_data()

    assert MOCK_SERIAL in data
    assert data[MOCK_SERIAL].status == "idle"
    # Nessun evento per transizione idle→idle
    mock_hass.bus.async_fire.assert_not_called()


@pytest.mark.asyncio
async def test_call_coordinator_doorbell_event(mock_hass, mock_config_entry) -> None:
    """Transizione idle→ringing deve emettere DOORBELL_PRESSED e CALL_STARTED."""
    device = make_mock_device()
    device_coord = MagicMock()
    device_coord.data = [device]
    device_coord.client = MagicMock()
    device_coord.client.get_call_status = AsyncMock(return_value=CallStatus(status="ringing"))

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    call_coord._prev_statuses = {MOCK_SERIAL: "idle"}
    mock_hass.bus.async_fire = MagicMock()

    await call_coord._async_update_data()

    fired_events = [call[0][0] for call in mock_hass.bus.async_fire.call_args_list]
    assert EVENT_DOORBELL_PRESSED in fired_events
    assert EVENT_CALL_STARTED in fired_events


@pytest.mark.asyncio
async def test_call_coordinator_call_ended_event(mock_hass, mock_config_entry) -> None:
    """Transizione ringing→idle deve emettere CALL_ENDED."""
    device = make_mock_device()
    device_coord = MagicMock()
    device_coord.data = [device]
    device_coord.client = MagicMock()
    device_coord.client.get_call_status = AsyncMock(return_value=CallStatus(status="idle"))

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    call_coord._prev_statuses = {MOCK_SERIAL: "ringing"}
    mock_hass.bus.async_fire = MagicMock()

    await call_coord._async_update_data()

    fired_events = [call[0][0] for call in mock_hass.bus.async_fire.call_args_list]
    assert EVENT_CALL_ENDED in fired_events


@pytest.mark.asyncio
async def test_call_coordinator_adaptive_interval_ringing(mock_hass) -> None:
    """Durante ringing l'intervallo deve scendere a 3s."""
    device = make_mock_device()
    device_coord = MagicMock()
    device_coord.data = [device]
    device_coord.client = MagicMock()
    device_coord.client.get_call_status = AsyncMock(return_value=CallStatus(status="ringing"))

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    call_coord._prev_statuses = {MOCK_SERIAL: "ringing"}
    mock_hass.bus.async_fire = MagicMock()

    await call_coord._async_update_data()

    assert call_coord.update_interval == timedelta(seconds=DEFAULT_CALL_POLL_INTERVAL_RINGING)


@pytest.mark.asyncio
async def test_call_coordinator_adaptive_interval_idle(mock_hass) -> None:
    """In idle l'intervallo deve tornare a 30s."""
    device = make_mock_device()
    device_coord = MagicMock()
    device_coord.data = [device]
    device_coord.client = MagicMock()
    device_coord.client.get_call_status = AsyncMock(return_value=CallStatus(status="idle"))

    call_coord = HikvisionEyCallStatusCoordinator(mock_hass, device_coord)
    # Simula che eravamo in ringing
    call_coord.update_interval = timedelta(seconds=3)
    call_coord._prev_statuses = {MOCK_SERIAL: "idle"}
    mock_hass.bus.async_fire = MagicMock()

    await call_coord._async_update_data()

    assert call_coord.update_interval == timedelta(seconds=DEFAULT_CALL_POLL_INTERVAL_IDLE)
