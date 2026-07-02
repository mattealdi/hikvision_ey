"""Registrazione e handler dei servizi HA per Hikvision EY."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api.exceptions import HikvisionEyError, UnlockFailed
from .const import (
    CONF_PREFERRED_STRATEGY,
    DOMAIN,
    EVENT_GATE_OPENED,
    EVENT_UNLOCK_FAILED,
    STRATEGY_AUTO,
    STRATEGY_LIST,
)

_LOGGER = logging.getLogger(__name__)

# ── Schema dei servizi ────────────────────────────────────────────────────────

OPEN_GATE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("lock_index", default=0): vol.All(int, vol.Range(min=0, max=9)),
        vol.Optional("strategy", default=STRATEGY_AUTO): vol.In(STRATEGY_LIST),
    }
)

DEVICE_ID_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
    }
)

EMPTY_SCHEMA = vol.Schema({})


# ── Helper per recuperare il coordinator dall'entry ───────────────────────────


def _get_coordinator(hass: HomeAssistant, entry_id: str | None = None):  # type: ignore[return]
    """Return the device coordinator for a given entry_id (or first available)."""
    integration_data = hass.data.get(DOMAIN, {})
    if entry_id:
        return integration_data.get(entry_id, {}).get("device_coordinator")
    # Prendi il primo disponibile
    for _eid, data in integration_data.items():
        coord = data.get("device_coordinator")
        if coord:
            return coord
    return None


def _find_device_by_serial(coordinator, serial: str):  # type: ignore[return]
    """Find a DeviceInfo by serial in coordinator data."""
    if not coordinator or not coordinator.data:
        return None
    for dev in coordinator.data:
        if dev.serial == serial or dev.full_serial == serial:
            return dev
    return None


# ── Handler dei servizi ────────────────────────────────────────────────────────


async def _handle_open_gate(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the open_gate service call.

    Args:
        hass: Home Assistant instance.
        call: ServiceCall with device_id, lock_index, strategy.
    """
    serial = call.data["device_id"]
    lock_index: int = call.data["lock_index"]
    strategy: str = call.data["strategy"]

    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")

    device = _find_device_by_serial(coordinator, serial)
    channel = 1  # default
    if device and device.cameras:
        # Usa il primo canale camera disponibile
        channel = device.cameras[0].channel_number

    _LOGGER.info(
        "[Service] open_gate: device=%s channel=%d lock=%d strategy=%s",
        serial, channel, lock_index, strategy,
    )

    try:
        result = await coordinator.unlock_manager.open_gate(
            serial=serial,
            channel=channel,
            lock_index=lock_index,
            strategy=strategy,
        )
    except UnlockFailed as exc:
        _LOGGER.error("[Service] open_gate failed for %s: %s", serial, exc)
        hass.bus.async_fire(
            EVENT_UNLOCK_FAILED,
            {
                "device_id": serial,
                "serial": serial,
                "strategy": strategy,
                "error": str(exc),
                "timestamp": _now_iso(),
            },
        )
        raise HomeAssistantError(f"Failed to open gate: {exc}") from exc
    except HikvisionEyError as exc:
        raise HomeAssistantError(str(exc)) from exc

    if result.success:
        # Salva la strategia vincente nelle opzioni
        if result.strategy != strategy or strategy == STRATEGY_AUTO:
            coordinator.update_preferred_strategy(result.strategy)

        hass.bus.async_fire(
            EVENT_GATE_OPENED,
            {
                "device_id": serial,
                "serial": serial,
                "strategy": result.strategy,
                "lock_index": lock_index,
                "timestamp": _now_iso(),
            },
        )
        _LOGGER.info(
            "[Service] open_gate SUCCESS: device=%s strategy=%s",
            serial, result.strategy,
        )
    else:
        hass.bus.async_fire(
            EVENT_UNLOCK_FAILED,
            {
                "device_id": serial,
                "serial": serial,
                "strategy": result.strategy,
                "error": result.error,
                "timestamp": _now_iso(),
            },
        )


async def _handle_answer_call(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the answer_call service call."""
    serial = call.data["device_id"]
    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")
    try:
        await coordinator.client.answer_call(serial)
        _LOGGER.info("[Service] answer_call: %s", serial)
    except HikvisionEyError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def _handle_hangup(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the hangup service call."""
    serial = call.data["device_id"]
    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")
    try:
        await coordinator.client.hangup_call(serial)
        _LOGGER.info("[Service] hangup: %s", serial)
    except HikvisionEyError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def _handle_restart(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the restart service call."""
    serial = call.data["device_id"]
    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")
    try:
        await coordinator.client.restart_device(serial)
        _LOGGER.info("[Service] restart: %s", serial)
    except HikvisionEyError as exc:
        raise HomeAssistantError(str(exc)) from exc


async def _handle_refresh(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the refresh service call — force immediate coordinator update."""
    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")
    await coordinator.async_request_refresh()
    _LOGGER.info("[Service] refresh: coordinator update requested")


async def _handle_reconnect(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle the reconnect service call — force full re-login."""
    coordinator = _get_coordinator(hass)
    if coordinator is None:
        raise HomeAssistantError(f"{DOMAIN} coordinator not available")
    coordinator._is_logged_in = False  # noqa: SLF001 (interno al package)
    await coordinator.async_request_refresh()
    _LOGGER.info("[Service] reconnect: forced re-login")


# ── Registrazione ─────────────────────────────────────────────────────────────


def async_register_services(hass: HomeAssistant) -> None:
    """Register all hikvision_ey services.

    Args:
        hass: Home Assistant instance.
    """
    if hass.services.has_service(DOMAIN, "open_gate"):
        return  # Già registrati

    hass.services.async_register(
        DOMAIN, "open_gate", lambda call: _handle_open_gate(hass, call), schema=OPEN_GATE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "answer_call", lambda call: _handle_answer_call(hass, call), schema=DEVICE_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "hangup", lambda call: _handle_hangup(hass, call), schema=DEVICE_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "restart", lambda call: _handle_restart(hass, call), schema=DEVICE_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "refresh", lambda call: _handle_refresh(hass, call), schema=EMPTY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "reconnect", lambda call: _handle_reconnect(hass, call), schema=EMPTY_SCHEMA
    )
    _LOGGER.debug("[Services] All %s services registered", DOMAIN)


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister all hikvision_ey services (called on unload).

    Args:
        hass: Home Assistant instance.
    """
    for service in ("open_gate", "answer_call", "hangup", "restart", "refresh", "reconnect"):
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("[Services] All %s services unregistered", DOMAIN)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
