"""Test per i servizi HA di Hikvision EY (open_gate, hangup, refresh)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.hikvision_ey.api.exceptions import UnlockFailed
from custom_components.hikvision_ey.api.models import UnlockResult
from custom_components.hikvision_ey.const import (
    DOMAIN,
    EVENT_GATE_OPENED,
    EVENT_UNLOCK_FAILED,
    STRATEGY_AUTO,
    STRATEGY_CLOUD_A3,
)
from custom_components.hikvision_ey.services import (
    _get_coordinator,
    _handle_hangup,
    _handle_open_gate,
    _handle_reconnect,
    _handle_refresh,
    async_register_services,
    async_unregister_services,
)
from tests.conftest import MOCK_SERIAL


# ── Fixtures per i servizi ────────────────────────────────────────────────────


def make_mock_coordinator(success: bool = True, strategy: str = STRATEGY_CLOUD_A3):
    """Crea un coordinator mock con unlock_manager configurato."""
    coordinator = MagicMock()

    if success:
        unlock_result = UnlockResult(success=True, strategy=strategy, meta_code=200)
    else:
        unlock_result = UnlockResult(success=False, strategy=strategy, error="meta.code=10001")

    coordinator.unlock_manager = MagicMock()
    coordinator.unlock_manager.open_gate = AsyncMock(return_value=unlock_result)
    coordinator.update_preferred_strategy = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.hangup_call = AsyncMock()
    coordinator.client.answer_call = AsyncMock()
    coordinator.client.restart_device = AsyncMock()
    coordinator.client.refresh_login = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    # Simula un device nel data
    from custom_components.hikvision_ey.api.models import CameraInfo, DeviceInfo
    device = DeviceInfo(
        full_serial=f"{MOCK_SERIAL}_FULL",
        serial=MOCK_SERIAL,
        name="Test",
        device_type="DS-KV7413EY-IME2",
        firmware_version="V2",
        is_online=True,
        local_ip=None,
        wan_ip=None,
        wifi_signal=None,
        update_available=False,
        locks={1: 1},
        cameras=[
            CameraInfo(
                camera_id=f"{MOCK_SERIAL}-1",
                name="Cam",
                channel_number=1,
                signal_status=1,
                is_shown=True,
            )
        ],
    )
    coordinator.data = [device]

    return coordinator


def make_mock_hass(coordinator):
    """Crea un mock hass con il coordinator registrato."""
    hass = MagicMock()
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    hass.data = {
        DOMAIN: {
            "test_entry_id": {
                "device_coordinator": coordinator,
            }
        }
    }
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()
    return hass


# ── Test open_gate ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_gate_success() -> None:
    """open_gate con successo deve emettere EVENT_GATE_OPENED."""
    coordinator = make_mock_coordinator(success=True, strategy=STRATEGY_CLOUD_A3)
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {
        "device_id": MOCK_SERIAL,
        "lock_index": 0,
        "strategy": STRATEGY_AUTO,
    }

    await _handle_open_gate(hass, call)

    coordinator.unlock_manager.open_gate.assert_called_once_with(
        serial=MOCK_SERIAL,
        channel=1,
        lock_index=0,
        strategy=STRATEGY_AUTO,
    )

    fired_events = [c[0][0] for c in hass.bus.async_fire.call_args_list]
    assert EVENT_GATE_OPENED in fired_events


@pytest.mark.asyncio
async def test_open_gate_unlock_failed_fires_event() -> None:
    """open_gate con UnlockFailed deve emettere EVENT_UNLOCK_FAILED e sollevare HA error."""
    coordinator = make_mock_coordinator()
    coordinator.unlock_manager.open_gate = AsyncMock(
        side_effect=UnlockFailed("all strategies")
    )
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {
        "device_id": MOCK_SERIAL,
        "lock_index": 0,
        "strategy": STRATEGY_AUTO,
    }

    with pytest.raises(HomeAssistantError):
        await _handle_open_gate(hass, call)

    fired_events = [c[0][0] for c in hass.bus.async_fire.call_args_list]
    assert EVENT_UNLOCK_FAILED in fired_events


@pytest.mark.asyncio
async def test_open_gate_saves_preferred_strategy() -> None:
    """open_gate di successo deve chiamare update_preferred_strategy."""
    coordinator = make_mock_coordinator(success=True, strategy=STRATEGY_CLOUD_A3)
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {
        "device_id": MOCK_SERIAL,
        "lock_index": 0,
        "strategy": STRATEGY_AUTO,
    }

    await _handle_open_gate(hass, call)

    coordinator.update_preferred_strategy.assert_called_once_with(STRATEGY_CLOUD_A3)


@pytest.mark.asyncio
async def test_open_gate_no_coordinator() -> None:
    """open_gate senza coordinator deve sollevare HomeAssistantError."""
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()

    call = MagicMock()
    call.data = {
        "device_id": MOCK_SERIAL,
        "lock_index": 0,
        "strategy": STRATEGY_AUTO,
    }

    with pytest.raises(HomeAssistantError):
        await _handle_open_gate(hass, call)


# ── Test hangup ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hangup_success() -> None:
    """hangup deve chiamare hangup_call sul client."""
    coordinator = make_mock_coordinator()
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {"device_id": MOCK_SERIAL}

    await _handle_hangup(hass, call)

    coordinator.client.hangup_call.assert_called_once_with(MOCK_SERIAL)


# ── Test refresh ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_triggers_coordinator_update() -> None:
    """refresh deve chiamare async_request_refresh sul coordinator."""
    coordinator = make_mock_coordinator()
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {}

    await _handle_refresh(hass, call)

    coordinator.async_request_refresh.assert_called_once()


# ── Test reconnect ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconnect_forces_relogin() -> None:
    """reconnect deve azzerare _is_logged_in e fare refresh del coordinator."""
    coordinator = make_mock_coordinator()
    coordinator._is_logged_in = True
    hass = make_mock_hass(coordinator)

    call = MagicMock()
    call.data = {}

    await _handle_reconnect(hass, call)

    assert coordinator._is_logged_in is False
    coordinator.async_request_refresh.assert_called_once()


# ── Test service registration ─────────────────────────────────────────────────


def test_register_services() -> None:
    """async_register_services deve registrare tutti i 6 servizi."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_register = MagicMock()

    async_register_services(hass)

    assert hass.services.async_register.call_count == 6
    registered_services = {
        call[0][1] for call in hass.services.async_register.call_args_list
    }
    assert registered_services == {
        "open_gate",
        "answer_call",
        "hangup",
        "restart",
        "refresh",
        "reconnect",
    }


def test_register_services_idempotent() -> None:
    """async_register_services non deve registrare due volte."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)  # già registrati
    hass.services.async_register = MagicMock()

    async_register_services(hass)

    # Non deve registrare nulla
    hass.services.async_register.assert_not_called()


def test_unregister_services() -> None:
    """async_unregister_services deve rimuovere tutti i 6 servizi."""
    hass = MagicMock()
    hass.services = MagicMock()
    hass.services.async_remove = MagicMock()

    async_unregister_services(hass)

    assert hass.services.async_remove.call_count == 6
