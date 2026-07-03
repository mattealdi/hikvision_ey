"""Sensor entities per l'integrazione Hikvision EY.

12 sensor:
- firmware              — versione firmware
- device_name           — nome dispositivo
- serial                — numero seriale (breve)
- cloud_status          — stato cloud testuale
- last_call             — ultimo stato chiamata
- last_event            — ultimo evento (doorbell/call)
- call_count            — contatore chiamate sessione
- wifi_quality          — qualità WiFi (percentuale 0-100%)
- wifi_address          — IP locale/WiFi
- unlock_last_esito     — diagnostica: esito ultima apertura (ok/bug_nullpoint/timeout/errore/ignorato_cooldown/scartato_tardivo)
- unlock_last_durata_ms — diagnostica: durata ultima apertura in millisecondi
- unlock_last_strategia — diagnostica: strategia usata nell'ultima apertura
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HikvisionEyDeviceCoordinator
from .entity import HikvisionEyEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HikvisionEySensorDescription(SensorEntityDescription):
    """Extended sensor description."""

    value_key: str = ""
    """Key in device data to extract value from."""


SENSOR_DESCRIPTIONS: tuple[HikvisionEySensorDescription, ...] = (
    HikvisionEySensorDescription(
        key="firmware",
        translation_key="firmware",
        icon="mdi:chip",
        value_key="firmware_version",
    ),
    HikvisionEySensorDescription(
        key="device_name",
        translation_key="device_name",
        icon="mdi:video-input-component",
        value_key="name",
    ),
    HikvisionEySensorDescription(
        key="serial",
        translation_key="serial",
        icon="mdi:identifier",
        value_key="serial",
    ),
    HikvisionEySensorDescription(
        key="cloud_status",
        translation_key="cloud_status",
        icon="mdi:cloud",
        value_key="cloud_status_str",
    ),
    HikvisionEySensorDescription(
        key="last_call",
        translation_key="last_call",
        icon="mdi:phone-clock",
        value_key="last_call_status",
    ),
    HikvisionEySensorDescription(
        key="last_event",
        translation_key="last_event",
        icon="mdi:bell-ring",
        value_key="last_event_type",
    ),
    HikvisionEySensorDescription(
        key="call_count",
        translation_key="call_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_key="call_count",
    ),
    # Hik-Connect espone `signal` come percentuale 0-100 (NON dBm),
    # quindi usiamo unit=% e device_class None.
    HikvisionEySensorDescription(
        key="wifi_quality",
        translation_key="wifi_quality",
        icon="mdi:wifi",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="wifi_signal",
    ),
    HikvisionEySensorDescription(
        key="wifi_address",
        translation_key="wifi_address",
        icon="mdi:ip-network",
        value_key="local_ip",
    ),
    # ---- v0.3.6: sensori diagnostici apertura cancelletto ------------------
    # Esposti in categoria DIAGNOSTIC per non ingombrare la UI principale.
    # Aggiornati dal coordinator.open_gate_safely a ogni pressione bottone.
    HikvisionEySensorDescription(
        key="unlock_last_esito",
        translation_key="unlock_last_esito",
        icon="mdi:gate-open",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="unlock_esito",
    ),
    HikvisionEySensorDescription(
        key="unlock_last_durata_ms",
        translation_key="unlock_last_durata_ms",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_key="unlock_durata_ms",
    ),
    HikvisionEySensorDescription(
        key="unlock_last_strategia",
        translation_key="unlock_last_strategia",
        icon="mdi:strategy",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="unlock_strategia",
    ),
    # ---- v0.4.0: contatori chiamate --------------------------------------
    # Nella UI principale (non diagnostica) perché sono informazioni d'uso
    # utili, non solo debug.
    HikvisionEySensorDescription(
        key="calls_today",
        translation_key="calls_today",
        icon="mdi:phone-log",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_key="calls_today",
    ),
    HikvisionEySensorDescription(
        key="calls_total",
        translation_key="calls_total",
        icon="mdi:phone-log-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="calls_total",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hikvision EY sensor entities.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    device_coordinator: HikvisionEyDeviceCoordinator = entry_data["device_coordinator"]

    entities: list[HikvisionEySensor] = []
    if device_coordinator.data:
        for device in device_coordinator.data:
            for desc in SENSOR_DESCRIPTIONS:
                entities.append(
                    HikvisionEySensor(device_coordinator, device.serial, desc)
                )

    async_add_entities(entities)
    _LOGGER.debug("[Sensor] Added %d sensor entities", len(entities))


class HikvisionEySensor(HikvisionEyEntity, SensorEntity):
    """A sensor entity for Hikvision EY device data."""

    entity_description: HikvisionEySensorDescription

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        description: HikvisionEySensorDescription,
    ) -> None:
        """Initialize the sensor entity.

        Args:
            coordinator: Device coordinator.
            device_serial: Device serial number.
            description: Sensor entity description.
        """
        super().__init__(coordinator, device_serial, description.key)
        self.entity_description = description
        # Contatore chiamate in-memory per questa sessione
        self._call_count: int = 0

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor's current value."""
        dev = self._device_data
        if dev is None:
            return None

        key = self.entity_description.value_key

        # Campi diretti dal DeviceInfo
        if key == "firmware_version":
            return dev.firmware_version or None
        if key == "name":
            return dev.name
        if key == "serial":
            return dev.serial
        if key == "wifi_signal":
            return dev.wifi_signal
        if key == "local_ip":
            return dev.local_ip

        # Campi derivati
        if key == "cloud_status_str":
            if dev.is_online is True:
                return "online"
            if dev.is_online is False:
                return "offline"
            return "unknown"

        if key == "last_call_status":
            # Legge dall'ultimo dato del call coordinator se disponibile
            return None  # Sarà aggiornato dai listener sugli eventi

        if key == "last_event_type":
            return None  # Aggiornato tramite eventi HA

        if key == "call_count":
            return self._call_count

        # ---- v0.3.6: sensori diagnostici apertura --------------------------
        # Letti da coordinator.last_unlock_stats, aggiornato dal wrapper safety.
        if key.startswith("unlock_"):
            stats = getattr(self.coordinator, "last_unlock_stats", None) or {}
            if key == "unlock_esito":
                return stats.get("esito")
            if key == "unlock_durata_ms":
                return stats.get("durata_ms")
            if key == "unlock_strategia":
                return stats.get("strategia")

        # ---- v0.4.0: contatori chiamate ------------------------------------
        if key == "calls_today":
            return getattr(self.coordinator, "call_count_today", 0)
        if key == "calls_total":
            return getattr(self.coordinator, "call_count_total", 0)

        return None
