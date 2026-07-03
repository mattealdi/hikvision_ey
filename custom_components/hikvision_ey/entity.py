"""Base entity class per l'integrazione Hikvision EY."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import HikvisionEyDeviceCoordinator


class HikvisionEyEntity(CoordinatorEntity[HikvisionEyDeviceCoordinator]):
    """Base class per tutte le entità Hikvision EY.

    Caratteristiche:
    - Usa translation_key per nomi localizzati
    - Unique ID basato su serial + tipo entità
    - Device info linkato al device registry HA
    - Disponibilità basata su is_online del device
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        entity_suffix: str,
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator: Device coordinator instance.
            device_serial: Short serial number of the device.
            entity_suffix: Unique suffix for this entity type (e.g. 'open_gate').
        """
        super().__init__(coordinator)
        self._device_serial = device_serial
        self._attr_unique_id = f"{DOMAIN}_{device_serial}_{entity_suffix}"

    @property
    def _device_data(self):  # type: ignore[return]
        """Return the DeviceInfo for this entity's device."""
        if not self.coordinator.data:
            return None
        for dev in self.coordinator.data:
            if dev.serial == self._device_serial:
                return dev
        return None

    @property
    def available(self) -> bool:
        """Return True se il device è online in modo CONFERMATO.

        v0.5.0: semantica corretta a tre stati. Solo is_online is True
        (fresco/confermato) rende l'entità disponibile. None (sconosciuto,
        es. cloud stale dopo reboot) e False NON contano più come disponibili:
        così le entità device-bound non affermano "online" quando non lo sappiamo.
        Richiede anche l'ultimo update del coordinator riuscito.
        """
        if not self.coordinator.last_update_success:
            return False
        dev = self._device_data
        if dev is None:
            return False
        return dev.is_online is True

    @property
    def device_info(self) -> HADeviceInfo:
        """Return device info for the HA device registry."""
        dev = self._device_data
        if dev is None:
            return HADeviceInfo(
                identifiers={(DOMAIN, self._device_serial)},
                manufacturer=MANUFACTURER,
            )
        return HADeviceInfo(
            identifiers={(DOMAIN, dev.full_serial)},
            name=dev.name,
            manufacturer=MANUFACTURER,
            model=dev.device_type,
            sw_version=dev.firmware_version,
        )
