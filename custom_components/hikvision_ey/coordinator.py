"""DataUpdateCoordinator per l'integrazione Hikvision EY.

Due coordinator separati:
1. DeviceCoordinator — aggiornamento device list ogni 300s
2. CallStatusCoordinator — aggiornamento call status adattivo (3s ringing, 30s idle)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HikvisionEyClient, LocalISAPIClient, UnlockManager
from .api.exceptions import AuthError, CaptchaRequired, DeviceOffline, HikvisionEyError
from .api.models import CallStatus, DeviceInfo
from .const import (
    CONF_LOCAL_HOST,
    CONF_LOCAL_ISAPI_ENABLED,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_USERNAME,
    CONF_PASSWORD,
    CONF_PREFERRED_STRATEGY,
    CONF_REGION,
    CONF_TIMEOUT,
    CONF_USERNAME,
    DEFAULT_CALL_POLL_INTERVAL_IDLE,
    DEFAULT_CALL_POLL_INTERVAL_RINGING,
    DEFAULT_DEVICE_POLL_INTERVAL,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EVENT_CALL_ENDED,
    EVENT_CALL_STARTED,
    EVENT_CLOUD_RECONNECTED,
    EVENT_DOORBELL_PRESSED,
)

_LOGGER = logging.getLogger(__name__)


class HikvisionEyDeviceCoordinator(DataUpdateCoordinator[list[DeviceInfo]]):
    """Coordinator per l'aggiornamento periodico della lista device.

    Frequenza: ogni 300s (configurabile).
    Si occupa anche di mantenere la sessione cloud fresca (token refresh).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the device coordinator.

        Args:
            hass: Home Assistant instance.
            entry: Config entry with credentials and options.
        """
        self._entry = entry
        poll_interval = entry.options.get(
            "device_poll_interval", DEFAULT_DEVICE_POLL_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_devices",
            update_interval=timedelta(seconds=poll_interval),
        )

        cfg = entry.data
        opts = entry.options

        timeout = opts.get(CONF_TIMEOUT, cfg.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
        region = cfg.get(CONF_REGION, DEFAULT_REGION)

        # Client cloud
        self.client = HikvisionEyClient(region=region, timeout=timeout)
        self._username: str = cfg[CONF_USERNAME]
        self._password: str = cfg[CONF_PASSWORD]

        # Client ISAPI locale (opzionale)
        self.isapi_client: LocalISAPIClient | None = None
        if opts.get(CONF_LOCAL_ISAPI_ENABLED):
            self.isapi_client = LocalISAPIClient(
                host=opts[CONF_LOCAL_HOST],
                username=opts[CONF_LOCAL_USERNAME],
                password=opts[CONF_LOCAL_PASSWORD],
                timeout=timeout,
            )

        # Unlock manager
        # v0.3.3: se in options c'è una strategia legacy A1-A4 salvata
        # da versioni precedenti (bug: veniva memorizzata come 'successo' anche
        # quando cloud rispondeva 200 senza effettivo unlock sul bus), la
        # ripuliamo qui una tantum. cloud_verified è l'unica affidabile su EY.
        preferred = opts.get(CONF_PREFERRED_STRATEGY)
        _VALID_PREFERRED = {"cloud_verified", "local"}
        if preferred and preferred not in _VALID_PREFERRED and preferred != "auto":
            _LOGGER.warning(
                "[DeviceCoordinator] Clearing stale preferred_strategy=%s (was legacy A1-A4)",
                preferred,
            )
            new_options = {**entry.options}
            new_options.pop(CONF_PREFERRED_STRATEGY, None)
            hass.config_entries.async_update_entry(entry, options=new_options)
            preferred = None

        self.unlock_manager = UnlockManager(
            self.client,
            isapi_client=self.isapi_client,
            preferred_strategy=preferred,
        )

        self._is_logged_in = False
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    async def _async_update_data(self) -> list[DeviceInfo]:
        """Fetch updated device list from cloud.

        Returns:
            List of DeviceInfo dataclasses.

        Raises:
            ConfigEntryAuthFailed: On auth error (triggers reauth flow).
            UpdateFailed: On recoverable errors.
        """
        try:
            # Assicura login o refresh token
            if not self._is_logged_in:
                _LOGGER.debug("[DeviceCoordinator] Initial login")
                await self.client.login(self._username, self._password)
                self._is_logged_in = True
                self.hass.bus.async_fire(
                    EVENT_CLOUD_RECONNECTED,
                    {"domain": DOMAIN, "timestamp": _now_iso()},
                )
            else:
                await self.client.ensure_authenticated(self._username, self._password)

            devices = await self.client.get_devices()
            self._consecutive_errors = 0
            _LOGGER.debug("[DeviceCoordinator] Updated %d devices", len(devices))
            return devices

        except CaptchaRequired as exc:
            raise ConfigEntryAuthFailed(
                "Hik-Connect requires CAPTCHA — login via mobile app"
            ) from exc

        except AuthError as exc:
            self._is_logged_in = False
            raise ConfigEntryAuthFailed("Authentication failed") from exc

        except (HikvisionEyError, aiohttp.ClientError, OSError) as exc:
            self._consecutive_errors += 1
            _LOGGER.warning(
                "[DeviceCoordinator] Update failed (%d/%d): %s",
                self._consecutive_errors,
                self._max_consecutive_errors,
                exc,
            )
            raise UpdateFailed(f"Cannot reach Hik-Connect: {exc}") from exc

    async def async_close(self) -> None:
        """Close all client sessions."""
        await self.client.close()
        if self.isapi_client:
            await self.isapi_client.close()

    def update_preferred_strategy(self, strategy: str) -> None:
        """Persist the preferred unlock strategy in entry options.

        Args:
            strategy: Strategy name that succeeded last.
        """
        self.unlock_manager.preferred_strategy = strategy
        new_options = {**self._entry.options, CONF_PREFERRED_STRATEGY: strategy}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        _LOGGER.info("[DeviceCoordinator] Preferred strategy saved: %s", strategy)


class HikvisionEyCallStatusCoordinator(DataUpdateCoordinator[dict[str, CallStatus]]):
    """Coordinator per l'aggiornamento adattivo dello stato chiamate.

    Polling ogni 3s durante ringing, ogni 30s in idle.
    Genera eventi sul bus HA per doorbell, call started/ended.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device_coordinator: HikvisionEyDeviceCoordinator,
    ) -> None:
        """Initialize the call status coordinator.

        Args:
            hass: Home Assistant instance.
            device_coordinator: Parent device coordinator (shares the API client).
        """
        self._device_coordinator = device_coordinator
        self._client = device_coordinator.client
        self._prev_statuses: dict[str, str] = {}  # serial → status string

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_call_status",
            update_interval=timedelta(seconds=DEFAULT_CALL_POLL_INTERVAL_IDLE),
        )

    def _get_serials(self) -> list[str]:
        """Get serials of devices to poll for call status."""
        if not self._device_coordinator.data:
            return []
        return [d.serial for d in self._device_coordinator.data if d.is_online]

    async def _async_update_data(self) -> dict[str, CallStatus]:
        """Fetch call status for all online devices.

        Returns:
            Dict of serial → CallStatus.
        """
        results: dict[str, CallStatus] = {}
        serials = self._get_serials()

        if not serials:
            _LOGGER.debug("[CallStatusCoordinator] No online devices to poll")
            return results

        # Poll tutti i device in parallelo
        tasks = {serial: self._fetch_one(serial) for serial in serials}
        fetched = await asyncio.gather(*tasks.values(), return_exceptions=True)

        any_ringing = False
        for serial, result in zip(tasks.keys(), fetched):
            if isinstance(result, Exception):
                _LOGGER.debug("[CallStatusCoordinator] Failed to get status for %s: %s", serial, result)
                continue
            results[serial] = result
            if result.is_ringing or result.is_in_call:
                any_ringing = True
            self._handle_state_change(serial, result)

        # Intervallo adattivo
        self._adapt_interval(any_ringing)

        return results

    async def _fetch_one(self, serial: str) -> CallStatus:
        """Fetch call status for a single device."""
        try:
            return await self._client.get_call_status(serial)
        except DeviceOffline:
            return CallStatus(status="offline")
        except (HikvisionEyError, aiohttp.ClientError) as exc:
            raise UpdateFailed(f"Call status error for {serial}") from exc

    def _handle_state_change(self, serial: str, status: CallStatus) -> None:
        """Fire HA events when call state changes.

        Args:
            serial: Device serial number.
            status: New call status.
        """
        prev = self._prev_statuses.get(serial, "idle")
        curr = status.status

        if curr == prev:
            return  # nessun cambiamento

        _LOGGER.debug("[CallStatusCoordinator] %s: %s → %s", serial, prev, curr)

        base_payload: dict[str, Any] = {
            "device_id": serial,
            "serial": serial,
            "timestamp": _now_iso(),
        }

        if curr == "ringing" and prev != "ringing":
            _LOGGER.info("[CallStatusCoordinator] Doorbell pressed: %s", serial)
            self.hass.bus.async_fire(EVENT_DOORBELL_PRESSED, {**base_payload, "channel": status.device_number})
            self.hass.bus.async_fire(EVENT_CALL_STARTED, {**base_payload, "channel": status.device_number})

        elif curr == "in_call" and prev not in ("in_call",):
            if prev != "ringing":
                self.hass.bus.async_fire(EVENT_CALL_STARTED, {**base_payload, "channel": status.device_number})

        elif curr == "idle" and prev in ("ringing", "in_call"):
            self.hass.bus.async_fire(EVENT_CALL_ENDED, {**base_payload})

        self._prev_statuses[serial] = curr

    def _adapt_interval(self, any_ringing: bool) -> None:
        """Adjust polling interval based on current state.

        Args:
            any_ringing: True if at least one device is ringing/in call.
        """
        if any_ringing:
            new_interval = timedelta(seconds=DEFAULT_CALL_POLL_INTERVAL_RINGING)
        else:
            new_interval = timedelta(seconds=DEFAULT_CALL_POLL_INTERVAL_IDLE)

        if self.update_interval != new_interval:
            _LOGGER.debug(
                "[CallStatusCoordinator] Interval changed to %ss",
                new_interval.total_seconds(),
            )
            self.update_interval = new_interval


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
