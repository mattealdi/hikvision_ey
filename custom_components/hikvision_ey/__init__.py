"""Integrazione Home Assistant per Hikvision EY — videocitofoni serie EY su cloud Hik-Connect."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .api.exceptions import AuthError, CaptchaRequired, HikvisionEyError
from .const import DOMAIN, PLATFORMS
from .coordinator import HikvisionEyCallStatusCoordinator, HikvisionEyDeviceCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hikvision EY from a config entry.

    Crea i due coordinator (device + call status), esegue il primo aggiornamento
    e registra i servizi HA.

    Args:
        hass: Home Assistant instance.
        entry: Config entry with credentials.

    Returns:
        True on success.

    Raises:
        ConfigEntryAuthFailed: On auth error.
        ConfigEntryNotReady: On network/connection error.
    """
    _LOGGER.info("[Setup] Setting up %s entry %s", DOMAIN, entry.entry_id)

    # Pulizia entity registry: rimuove entità camera orfane e sensori
    # deprecati da versioni precedenti (v0.2.x / v0.3.0)
    _async_cleanup_stale_entities(hass, entry)

    # Coordinator per la lista device
    device_coordinator = HikvisionEyDeviceCoordinator(hass, entry)

    # Primo aggiornamento — questo effettua il login
    try:
        await device_coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await device_coordinator.async_close()
        raise
    except Exception as exc:
        await device_coordinator.async_close()
        raise ConfigEntryNotReady(f"Cannot connect to Hik-Connect: {exc}") from exc

    # Coordinator per lo stato chiamate (condivide il client cloud)
    call_coordinator = HikvisionEyCallStatusCoordinator(hass, device_coordinator)
    await call_coordinator.async_config_entry_first_refresh()

    # Salva in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "device_coordinator": device_coordinator,
        "call_coordinator": call_coordinator,
    }

    # Forward alle piattaforme
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Registra servizi HA
    async_register_services(hass)

    # Aggiorna i coordinator quando cambiano le opzioni
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("[Setup] %s setup complete for entry %s", DOMAIN, entry.entry_id)
    return True


def _async_cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rimuove entità orfane lasciate da versioni precedenti.

    Storico:
      - v0.3.1: rimossa piattaforma 'camera' e sensore 'uptime_info'
      - v0.3.2: rimosso sensore 'rssi' (rinominato 'wifi_quality' con
        unit %) e binary sensor 'monitor_online' / 'outdoor_online'
        (duplicavano 'online')

    Questa funzione elimina tali entità dal registry al primo avvio
    dopo l'aggiornamento, così l'utente non si ritrova più la lista
    di entità fantasma "Non disponibile".
    """
    registry = er.async_get(hass)
    stale_platforms = {"camera"}
    stale_unique_id_suffixes = {
        "_uptime_info",
        "_rssi",
        "_monitor_online",
        "_outdoor_online",
    }
    removed = 0
    for entity in list(registry.entities.values()):
        if entity.config_entry_id != entry.entry_id:
            continue
        if entity.platform != DOMAIN:
            continue
        should_remove = entity.domain in stale_platforms or any(
            entity.unique_id.endswith(suffix) for suffix in stale_unique_id_suffixes
        )
        if should_remove:
            registry.async_remove(entity.entity_id)
            removed += 1
    if removed:
        _LOGGER.info("[Setup] Rimosse %d entità obsolete dal registry", removed)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload entry to apply new settings.

    Args:
        hass: Home Assistant instance.
        entry: Updated config entry.
    """
    _LOGGER.debug("[Setup] Options updated, reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Hikvision EY config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.

    Returns:
        True if unload was successful.
    """
    _LOGGER.info("[Setup] Unloading %s entry %s", DOMAIN, entry.entry_id)

    # Scarica le piattaforme
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        device_coordinator = entry_data.get("device_coordinator")
        if device_coordinator:
            await device_coordinator.async_close()

        # Deregistra servizi solo se non ci sono altre entry
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)

    return unload_ok
