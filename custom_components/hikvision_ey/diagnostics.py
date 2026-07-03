"""Diagnostica per l'integrazione Hikvision EY.

Espone dati di debug tramite HA diagnostics download,
con redact automatico di password, token e serial completo.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Campi da oscurare automaticamente nella diagnostica
TO_REDACT: set[str] = {
    "password",
    "local_password",
    "sessionId",
    "rfSessionId",
    "refreshSessionId",
    "token",
    "access_token",
    "full_serial",
    "fullSerial",
    "serial",  # oscuriamo anche il serial per privacy
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics data for a config entry.

    Args:
        hass: Home Assistant instance.
        config_entry: The config entry to diagnose.

    Returns:
        Sanitized diagnostics dict.
    """
    integration_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})
    device_coordinator = integration_data.get("device_coordinator")
    call_coordinator = integration_data.get("call_coordinator")

    devices_info: list[dict[str, Any]] = []
    if device_coordinator and device_coordinator.data:
        for dev in device_coordinator.data:
            devices_info.append(
                {
                    "serial": dev.serial,
                    "name": dev.name,
                    "device_type": dev.device_type,
                    "firmware_version": dev.firmware_version,
                    "is_online": dev.is_online,
                    "local_ip": dev.local_ip,
                    "wan_ip": dev.wan_ip,
                    "wifi_signal": dev.wifi_signal,
                    "update_available": dev.update_available,
                    "locks": dev.locks,
                    "cameras_count": len(dev.cameras),
                }
            )

    call_statuses: dict[str, Any] = {}
    if call_coordinator and call_coordinator.data:
        for serial, cs in call_coordinator.data.items():
            call_statuses[serial] = {
                "status": cs.status,
                "is_ringing": cs.is_ringing,
                "is_in_call": cs.is_in_call,
            }

    client_info: dict[str, Any] = {}
    if device_coordinator:
        client = device_coordinator.client
        valid_until = client.login_valid_until
        client_info = {
            "base_url": client.base_url,
            "login_valid_until": valid_until.isoformat() if valid_until else None,
            "needs_refresh": client.needs_token_refresh(),
            "preferred_strategy": device_coordinator.unlock_manager.preferred_strategy,
        }

    # v0.4.0: dump esteso apertura cancelletto (ultima + storico ultime 10
    # + contatori chiamate). Utile per capire tempi reali / bug NULLpoint
    # da inviare al supporto Hikvision.
    unlock_info: dict[str, Any] = {}
    if device_coordinator:
        unlock_info = {
            "is_unlocking": device_coordinator.is_unlocking,
            "last_unlock_stats": device_coordinator.last_unlock_stats,
            "unlock_history": list(device_coordinator.unlock_history),
            "call_count_today": device_coordinator.call_count_today,
            "call_count_total": device_coordinator.call_count_total,
            "config": {
                "timeout_s": device_coordinator._UNLOCK_TIMEOUT_S,
                "cooldown_ok_s": device_coordinator._UNLOCK_COOLDOWN_OK_S,
                "cooldown_fail_s": device_coordinator._UNLOCK_COOLDOWN_FAIL_S,
            },
        }

    diag: dict[str, Any] = {
        "config_entry": {
            "domain": config_entry.domain,
            "version": config_entry.version,
            "data": async_redact_data(dict(config_entry.data), TO_REDACT),
            "options": async_redact_data(dict(config_entry.options), TO_REDACT),
        },
        "client": client_info,
        "devices": async_redact_data(devices_info, TO_REDACT),
        "call_statuses": call_statuses,
        "unlock": unlock_info,
    }

    _LOGGER.debug("[Diagnostics] Generated diagnostics for entry %s", config_entry.entry_id)
    return diag
