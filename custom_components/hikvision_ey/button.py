"""Button entities per l'integrazione Hikvision EY.

7 button:
- apri_cancelletto (open_gate) — apre cancelletto con strategia auto
- apri_porta_1 (open_door_1) — apre porta/lock index 0
- apri_porta_2 (open_door_2) — apre porta/lock index 1
- rispondi (answer) — risponde alla chiamata
- riaggancia (hangup) — riaggancia la chiamata
- riavvia (restart) — riavvia dispositivo
- aggiorna_token (refresh_token) — forza refresh token cloud
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.exceptions import HikvisionEyError, UnlockFailed
from .const import DOMAIN, STRATEGY_AUTO
from .coordinator import HikvisionEyDeviceCoordinator
from .entity import HikvisionEyEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class HikvisionEyButtonDescription(ButtonEntityDescription):
    """Extended button description with press handler."""

    lock_index: int = 0
    strategy: str = STRATEGY_AUTO
    action: str = "open_gate"  # open_gate / answer / hangup / restart / refresh_token


BUTTON_DESCRIPTIONS: tuple[HikvisionEyButtonDescription, ...] = (
    HikvisionEyButtonDescription(
        key="open_gate",
        translation_key="open_gate",
        icon="mdi:gate-open",
        action="open_gate",
        lock_index=0,
        strategy=STRATEGY_AUTO,
    ),
    HikvisionEyButtonDescription(
        key="open_door_1",
        translation_key="open_door_1",
        icon="mdi:door-open",
        action="open_gate",
        lock_index=0,
        strategy=STRATEGY_AUTO,
    ),
    HikvisionEyButtonDescription(
        key="open_door_2",
        translation_key="open_door_2",
        icon="mdi:door-open",
        action="open_gate",
        lock_index=1,
        strategy=STRATEGY_AUTO,
    ),
    HikvisionEyButtonDescription(
        key="answer",
        translation_key="answer",
        icon="mdi:phone-in-talk",
        action="answer",
    ),
    HikvisionEyButtonDescription(
        key="hangup",
        translation_key="hangup",
        icon="mdi:phone-hangup",
        action="hangup",
    ),
    HikvisionEyButtonDescription(
        key="restart",
        translation_key="restart",
        icon="mdi:restart",
        action="restart",
    ),
    HikvisionEyButtonDescription(
        key="refresh_token",
        translation_key="refresh_token",
        icon="mdi:refresh",
        action="refresh_token",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hikvision EY button entities.

    Args:
        hass: Home Assistant instance.
        config_entry: Config entry.
        async_add_entities: Callback to add entities.
    """
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    device_coordinator: HikvisionEyDeviceCoordinator = entry_data["device_coordinator"]

    entities: list[HikvisionEyButton] = []
    if device_coordinator.data:
        for device in device_coordinator.data:
            for desc in BUTTON_DESCRIPTIONS:
                entities.append(
                    HikvisionEyButton(device_coordinator, device.serial, desc)
                )

    async_add_entities(entities)
    _LOGGER.debug("[Button] Added %d button entities", len(entities))


class HikvisionEyButton(HikvisionEyEntity, ButtonEntity):
    """A button entity for Hikvision EY device actions."""

    entity_description: HikvisionEyButtonDescription

    def __init__(
        self,
        coordinator: HikvisionEyDeviceCoordinator,
        device_serial: str,
        description: HikvisionEyButtonDescription,
    ) -> None:
        """Initialize the button entity.

        Args:
            coordinator: Device coordinator.
            device_serial: Device serial number.
            description: Button entity description.
        """
        super().__init__(coordinator, device_serial, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle button press.

        Raises:
            HomeAssistantError: On API error.
        """
        action = self.entity_description.action
        serial = self._device_serial
        coordinator = self.coordinator

        _LOGGER.debug("[Button] Press: action=%s device=%s", action, serial)

        try:
            if action == "open_gate":
                # Trova il canale dalla lista device
                device = self._device_data
                channel = 1
                if device and device.cameras:
                    channel = device.cameras[0].channel_number

                result = await coordinator.unlock_manager.open_gate(
                    serial=serial,
                    channel=channel,
                    lock_index=self.entity_description.lock_index,
                    strategy=self.entity_description.strategy,
                )
                if result.success:
                    coordinator.update_preferred_strategy(result.strategy)
                    _LOGGER.info("[Button] Gate opened: device=%s strategy=%s", serial, result.strategy)
                else:
                    _LOGGER.warning("[Button] Gate open failed: device=%s error=%s", serial, result.error)

            elif action == "answer":
                await coordinator.client.answer_call(serial)

            elif action == "hangup":
                await coordinator.client.hangup_call(serial)

            elif action == "restart":
                await coordinator.client.restart_device(serial)

            elif action == "refresh_token":
                await coordinator.client.refresh_login()
                _LOGGER.info("[Button] Token refreshed for %s", serial)

        except (HikvisionEyError, UnlockFailed) as exc:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError(f"Hikvision EY action failed: {exc}") from exc
