"""Camera entity per l'integrazione Hikvision EY.

Espone anteprima on-demand (snapshot JPEG via ISAPI) e stream RTSP live
dal monitor interno DS-KH7300EY sulla LAN, che riceve il video dal
pannello esterno DS-KV7413EY tramite bus 2-fili.

La camera entity è disponibile SOLO quando il client ISAPI locale è
configurato (serve IP + credenziali admin del monitor). Se l'integrazione
è configurata solo in modalità cloud, la piattaforma camera non registra
alcuna entity.
"""
from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
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

    Aggiunge una entity camera per ogni device che ha:
      - almeno una `camera.is_shown` nei dati cloud, E
      - il client ISAPI locale configurato (host + credenziali admin).
    """
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    device_coordinator: HikvisionEyDeviceCoordinator = entry_data["device_coordinator"]
    isapi_client = getattr(device_coordinator, "isapi_client", None)

    if isapi_client is None:
        _LOGGER.info(
            "[Camera] ISAPI locale non configurato — nessuna entity camera creata. "
            "Configura host + credenziali admin del monitor per abilitare stream/snapshot."
        )
        return

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
    """Camera entity per un canale del videocitofono Hikvision EY.

    - `stream_source()` restituisce l'URL RTSP live del monitor sulla LAN.
    - `async_camera_image()` fa uno snapshot JPEG on-demand via ISAPI.
    """

    _attr_has_entity_name = True
    _attr_is_streaming = True

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        camera_id: str,
        camera_name: str,
    ) -> None:
        """Initialize the camera entity."""
        HikvisionEyEntity.__init__(self, coordinator, device_serial, f"camera_{camera_id}")
        Camera.__init__(self)
        self._camera_id = camera_id
        self._camera_name = camera_name
        self._attr_translation_key = "camera"
        self._attr_name = camera_name

    # ---- Metadati -------------------------------------------------------

    @property
    def brand(self) -> str:
        """Brand della camera."""
        return "Hikvision"

    @property
    def model(self) -> str | None:
        """Modello dal cloud."""
        dev = self._device_data
        return dev.device_type if dev else None

    @property
    def is_on(self) -> bool:
        """True se la entity è attiva/disponibile."""
        return self.available

    @property
    def available(self) -> bool:
        """Disponibile se il device è online e ISAPI configurato."""
        isapi = getattr(self.coordinator, "isapi_client", None)
        if isapi is None:
            return False
        dev = self._device_data
        if dev is None:
            return False
        return dev.is_online is not False

    # ---- Stream / snapshot ---------------------------------------------

    async def stream_source(self) -> str | None:
        """Restituisce l'URL RTSP del monitor per lo stream live.

        Il canale 1 sul monitor DS-KH7300EY espone il video del pannello
        esterno DS-KV7413EY (collegato via bus 2-fili).
        """
        isapi = getattr(self.coordinator, "isapi_client", None)
        if isapi is None:
            _LOGGER.warning(
                "[Camera] stream_source: ISAPI locale non disponibile per %s",
                self._camera_id,
            )
            return None
        # Sub-stream (SD) = più leggero, latenza minore per anteprima citofono
        url = isapi.rtsp_stream_url(channel=1, stream=2)
        _LOGGER.debug(
            "[Camera] stream_source per %s: rtsp://***:***@%s:554/... (sub)",
            self._camera_id,
            isapi.host,
        )
        return url

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Snapshot JPEG on-demand via ISAPI /Streaming/channels/101/picture."""
        isapi = getattr(self.coordinator, "isapi_client", None)
        if isapi is None:
            _LOGGER.warning(
                "[Camera] snapshot: ISAPI locale non disponibile per %s",
                self._camera_id,
            )
            return None
        try:
            return await isapi.get_snapshot(channel=1)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "[Camera] snapshot fallito per %s: %s", self._camera_id, exc
            )
            return None
