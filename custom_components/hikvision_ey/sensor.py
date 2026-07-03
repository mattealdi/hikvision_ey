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
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import HikvisionEyDeviceCoordinator
from .entity import HikvisionEyEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HikvisionEySensorDescription(SensorEntityDescription):
    """Extended sensor description."""

    value_key: str = ""
    """Key in device data to extract value from."""
    coordinator_state: bool = False
    """v0.5.0: True se il valore dipende dallo stato interno del coordinator
    (apertura/contatori) e NON dal DeviceInfo cloud. Questi sensori sono
    sempre disponibili e persistenti tra i restart di HA."""


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
        coordinator_state=True,
    ),
    HikvisionEySensorDescription(
        key="unlock_last_durata_ms",
        translation_key="unlock_last_durata_ms",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        value_key="unlock_durata_ms",
        coordinator_state=True,
    ),
    HikvisionEySensorDescription(
        key="unlock_last_strategia",
        translation_key="unlock_last_strategia",
        icon="mdi:strategy",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="unlock_strategia",
        coordinator_state=True,
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
        coordinator_state=True,
    ),
    HikvisionEySensorDescription(
        key="calls_total",
        translation_key="calls_total",
        icon="mdi:phone-log-outline",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_key="calls_total",
        coordinator_state=True,
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

    entities: list[SensorEntity] = []
    if device_coordinator.data:
        for device in device_coordinator.data:
            for desc in SENSOR_DESCRIPTIONS:
                if desc.coordinator_state:
                    # v0.5.0: sensori legati allo stato interno del coordinator
                    # (apertura/contatori): sempre disponibili + persistenti.
                    entities.append(
                        HikvisionEyDiagnosticSensor(
                            device_coordinator, device.serial, device.full_serial, desc
                        )
                    )
                else:
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

    # v0.5.0: i sensori metadata (firmware/serial/device_name) e cloud_status
    # NON devono diventare "unavailable" quando il device è offline/sconosciuto:
    # sono informazioni identificative o proprio lo stato cloud, che deve poter
    # riportare "offline"/"unknown". Restano legati solo alla presenza del dato.
    _ALWAYS_AVAILABLE_KEYS = frozenset(
        {"firmware_version", "name", "serial", "cloud_status_str"}
    )

    @property
    def available(self) -> bool:
        """Return availability.

        v0.5.0: i sensori identificativi e cloud_status restano disponibili
        finché il coordinator ha dati, indipendentemente da is_online. Gli
        altri sensori device-bound (wifi_quality/wifi_address/last_*) seguono
        la semantica restrittiva della classe base.
        """
        if self.entity_description.value_key in self._ALWAYS_AVAILABLE_KEYS:
            return (
                self.coordinator.last_update_success
                and self._device_data is not None
            )
        return super().available

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

        return None


class HikvisionEyDiagnosticSensor(CoordinatorEntity[HikvisionEyDeviceCoordinator], RestoreSensor):
    """Sensore diagnostico/contatore legato allo stato interno del coordinator.

    v0.5.0 - risolve Bug B ("sconosciuto" dopo apertura riuscita):

    - NON dipende dal DeviceInfo cloud: legge esclusivamente da attributi del
      coordinator (last_unlock_stats, call_count_today/total). Nessun guard
      `dev is None`, quindi il valore appare anche quando il device è
      offline/sconosciuto lato cloud.
    - E' SEMPRE disponibile (available=True): questi sono dati locali
      dell'integrazione, non stato del dispositivo.
    - E' un RestoreSensor: il valore viene ripristinato dallo state HA al
      riavvio, così i contatori e l'ultimo esito non tornano "unknown" dopo
      un restart, prima ancora che il coordinator ricarichi lo stato persistito.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: HikvisionEySensorDescription

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        device_full_serial: str,
        description: HikvisionEySensorDescription,
    ) -> None:
        """Initialize the diagnostic sensor.

        Args:
            coordinator: Device coordinator.
            device_serial: Short serial (per unique_id, coerente con le altre entità).
            device_full_serial: Full serial (per il device registry link).
            description: Sensor entity description (coordinator_state=True).
        """
        super().__init__(coordinator)
        self.entity_description = description
        self._device_serial = device_serial
        self._device_full_serial = device_full_serial
        self._attr_unique_id = f"{DOMAIN}_{device_serial}_{description.key}"
        # Valore ripristinato dallo state precedente (fallback finché il
        # coordinator non ha ancora ricaricato lo stato persistito).
        self._restored_native_value: str | int | float | None = None

    async def async_added_to_hass(self) -> None:
        """Ripristina l'ultimo valore noto dallo state HA."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            self._restored_native_value = last_data.native_value
            _LOGGER.debug(
                "[DiagSensor] %s ripristinato a %s",
                self.entity_description.key,
                self._restored_native_value,
            )

    @property
    def available(self) -> bool:
        """Sempre disponibile: dato locale dell'integrazione, non del device."""
        return True

    @property
    def device_info(self) -> HADeviceInfo:
        """Link allo stesso device registry delle altre entità (full_serial)."""
        return HADeviceInfo(
            identifiers={(DOMAIN, self._device_full_serial)},
            manufacturer=MANUFACTURER,
        )

    @property
    def native_value(self) -> str | int | float | None:
        """Valore corrente letto dal coordinator, con fallback al ripristino."""
        key = self.entity_description.value_key

        # ---- sensori diagnostici apertura ---------------------------------
        if key.startswith("unlock_"):
            stats = getattr(self.coordinator, "last_unlock_stats", None) or {}
            if key == "unlock_esito":
                val = stats.get("esito")
            elif key == "unlock_durata_ms":
                val = stats.get("durata_ms")
            elif key == "unlock_strategia":
                val = stats.get("strategia")
            else:
                val = None
            if val is not None:
                return val
            return self._restored_native_value

        # ---- contatori chiamate -------------------------------------------
        if key == "calls_today":
            val = getattr(self.coordinator, "call_count_today", None)
            if val:
                return val
            # 0 legittimo dopo rollover/reset; il ripristino serve solo se il
            # coordinator non ha ancora caricato lo stato (val is None o 0 iniziale).
            if val == 0:
                return 0
            return self._restored_native_value
        if key == "calls_total":
            val = getattr(self.coordinator, "call_count_total", None)
            if val:
                return val
            if val == 0:
                return self._restored_native_value if self._restored_native_value is not None else 0
            return self._restored_native_value

        return self._restored_native_value
