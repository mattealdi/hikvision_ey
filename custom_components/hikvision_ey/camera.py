"""Camera entity per l'integrazione Hikvision EY.

Espone stream RTSP/HLS se disponibile tramite il cloud Hik-Connect.
Se lo stream non è disponibile, la entity è present ma unavailable,
con log warning per guidare l'utente.
"""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HikvisionEyDeviceCoordinator
from .entity import HikvisionEyEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hikvision EY camera entities.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    device_coordinator: HikvisionEyDeviceCoordinator = entry_data["device_coordinator"]

    entities: list[HikvisionEyCamera] = []
    if device_coordinator.data:
        for device in device_coordinator.data:
            for camera in device.cameras:
                if camera.is_shown:
                    entities.append(
                        HikvisionEyCamera(
                            device_coordinator,
                            device.serial,
                            camera.camera_id,
                            camera.name,
                        )
                    )

    async_add_entities(entities)
    _LOGGER.debug("[Camera] Added %d camera entities", len(entities))


class HikvisionEyCamera(HikvisionEyEntity, Camera):
    """Camera entity for a Hikvision EY device channel.

    Nota: lo stream RTSP/HLS tramite cloud Hik-Connect non è documentato
    pubblicamente. Questa entity è predisposta per futuro supporto.
    Se non disponibile, logga un warning e non fornisce stream.
    """

    _attr_has_entity_name = True
    _attr_is_streaming = False

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        camera_id: str,
        camera_name: str,
    ) -> None:
        """Initialize the camera entity.

        Args:
            coordinator: Device coordinator.
            device_serial: Device serial.
            camera_id: Camera ID from cloud.
            camera_name: Camera display name.
        """
        # Inizializza HikvisionEyEntity
        HikvisionEyEntity.__init__(self, coordinator, device_serial, f"camera_{camera_id}")
        # Inizializza Camera
        Camera.__init__(self)
        self._camera_id = camera_id
        self._camera_name = camera_name
        self._stream_url: str | None = None
        self._attr_translation_key = "camera"
        # Usa il nome della camera come nome entità
        self._attr_name = camera_name

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return still image from camera.

        Returns:
            Image bytes or None if unavailable.
        """
        # Lo snapshot via cloud non è attualmente supportato
        _LOGGER.warning(
            "[Camera] Still image not available for camera %s on device %s. "
            "Cloud RTSP stream requires Hik-Connect P2P credentials.",
            self._camera_id,
            self._device_serial,
        )
        return None

    @property
    def is_on(self) -> bool:
        """Return True if camera entity is active."""
        return self.available

    @property
    def brand(self) -> str:
        """Return camera brand."""
        return "Hikvision"

    @property
    def model(self) -> str | None:
        """Return camera model from device info."""
        dev = self._device_data
        return dev.device_type if dev else None

    @property
    def available(self) -> bool:
        """Return True if device is online."""
        dev = self._device_data
        if dev is None:
            return False
        return dev.is_online is not False

    async def stream_source(self) -> str | None:
        """Return the stream URL if available.

        Returns:
            RTSP/HLS URL or None if not available.
        """
        if self._stream_url:
            return self._stream_url
        # Segnala che il cloud stream non è ancora supportato
        _LOGGER.warning(
            "[Camera] Stream source not available for camera %s. "
            "To enable live view, configure local ISAPI with RTSP credentials.",
            self._camera_id,
        )
        return None
