"""Binary sensor entities per l'integrazione Hikvision EY.

4 binary_sensor:
- online           — device online sul cloud
- cloud_connected  — cloud reachable (basato su coordinator)
- is_ringing       — campanello premuto (True se ringing)
- in_call          — chiamata in corso

NOTE v0.3.2: rimossi 'monitor_online' e 'outdoor_online' perché il
coordinator li impostava entrambi al valore di dev.is_online, duplicando
esattamente il sensore 'online'. Erano rumore, non informazione utile.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HikvisionEyCallStatusCoordinator, HikvisionEyDeviceCoordinator
from .entity import HikvisionEyEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HikvisionEyBinarySensorDescription(BinarySensorEntityDescription):
    """Extended binary sensor description."""

    sensor_type: str = "device"  # "device" o "call"


DEVICE_BINARY_SENSOR_DESCRIPTIONS: tuple[HikvisionEyBinarySensorDescription, ...] = (
    HikvisionEyBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        sensor_type="device",
    ),
    HikvisionEyBinarySensorDescription(
        key="cloud_connected",
        translation_key="cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        sensor_type="device",
        icon="mdi:cloud-check",
    ),
)

CALL_BINARY_SENSOR_DESCRIPTIONS: tuple[HikvisionEyBinarySensorDescription, ...] = (
    HikvisionEyBinarySensorDescription(
        key="is_ringing",
        translation_key="is_ringing",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        sensor_type="call",
        icon="mdi:doorbell",
    ),
    HikvisionEyBinarySensorDescription(
        key="in_call",
        translation_key="in_call",
        device_class=BinarySensorDeviceClass.SOUND,
        sensor_type="call",
        icon="mdi:phone-in-talk",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hikvision EY binary sensor entities.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    device_coordinator: HikvisionEyDeviceCoordinator = entry_data["device_coordinator"]
    call_coordinator: HikvisionEyCallStatusCoordinator = entry_data["call_coordinator"]

    entities: list[HikvisionEyBinarySensor] = []

    if device_coordinator.data:
        for device in device_coordinator.data:
            # Sensori "device" usano device_coordinator
            for desc in DEVICE_BINARY_SENSOR_DESCRIPTIONS:
                entities.append(
                    HikvisionEyDeviceBinarySensor(device_coordinator, device.serial, desc)
                )
            # Sensori "call" usano call_coordinator
            for desc in CALL_BINARY_SENSOR_DESCRIPTIONS:
                if desc.sensor_type == "call":
                    entities.append(
                        HikvisionEyCallBinarySensor(
                            device_coordinator, call_coordinator, device.serial, desc
                        )
                    )

    async_add_entities(entities)
    _LOGGER.debug("[BinarySensor] Added %d binary sensor entities", len(entities))


class HikvisionEyBinarySensor(HikvisionEyEntity, BinarySensorEntity):
    """Base binary sensor entity."""

    entity_description: HikvisionEyBinarySensorDescription


class HikvisionEyDeviceBinarySensor(HikvisionEyBinarySensor):
    """Binary sensor driven by device coordinator data."""

    def __init__(
        self,
        device_coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        description: HikvisionEyBinarySensorDescription,
    ) -> None:
        """Initialize the device binary sensor.

        v0.3.4: Fix critico — la versione precedente non assegnava
        `self.entity_description`, causando AttributeError a ogni update
        del coordinator (1984+ occorrenze osservate in 7 ore su v0.3.2/3).
        """
        super().__init__(device_coordinator, device_serial, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return sensor state based on device data."""
        dev = self._device_data
        key = self.entity_description.key

        if dev is None:
            return None

        if key == "online":
            return dev.is_online

        if key == "cloud_connected":
            # True se coordinator ha dati validi e device è online
            return bool(self.coordinator.data) and bool(dev.is_online)

        return None


class HikvisionEyCallBinarySensor(HikvisionEyBinarySensor):
    """Binary sensor driven by call status coordinator data."""

    def __init__(
        self,
        device_coordinator: HikvisionEyDeviceCoordinator,
        call_coordinator: HikvisionEyCallStatusCoordinator,
        device_serial: str,
        description: HikvisionEyBinarySensorDescription,
    ) -> None:
        """Initialize the call binary sensor.

        Args:
            device_coordinator: Device coordinator (for device_info).
            call_coordinator: Call status coordinator.
            device_serial: Device serial.
            description: Entity description.

        v0.3.4: Fix critico — la versione precedente non assegnava
        `self.entity_description`, causando AttributeError a ogni update
        del coordinator.
        """
        super().__init__(device_coordinator, device_serial, description.key)
        self.entity_description = description
        self._call_coordinator = call_coordinator

    async def async_added_to_hass(self) -> None:
        """Subscribe to call coordinator updates too."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._call_coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool | None:
        """Return sensor state based on call status data."""
        if not self._call_coordinator.data:
            return None
        call_status = self._call_coordinator.data.get(self._device_serial)
        if call_status is None:
            return None

        key = self.entity_description.key
        if key == "is_ringing":
            return call_status.is_ringing
        if key == "in_call":
            return call_status.is_in_call
        return None
