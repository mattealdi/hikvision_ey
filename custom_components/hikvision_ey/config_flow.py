"""Config flow per l'integrazione Hikvision EY."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import HikvisionEyClient
from .api.exceptions import AuthError, CaptchaRequired, HikvisionEyError
from .const import (
    CONF_LOCAL_HOST,
    CONF_LOCAL_ISAPI_ENABLED,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_USERNAME,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_TIMEOUT,
    CONF_USERNAME,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT,
    DOMAIN,
    REGION_LIST,
)

_LOGGER = logging.getLogger(__name__)

# Schema per il passo "user" (credenziali cloud)
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(REGION_LIST),
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
            int, vol.Range(min=5, max=120)
        ),
    }
)

# Schema per OptionsFlow — sezione ISAPI locale opzionale
STEP_OPTIONS_BASE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_LOCAL_ISAPI_ENABLED, default=False): bool,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(
            int, vol.Range(min=5, max=120)
        ),
    }
)

STEP_OPTIONS_LOCAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOCAL_HOST): str,
        vol.Required(CONF_LOCAL_USERNAME, default="admin"): str,
        vol.Required(CONF_LOCAL_PASSWORD): str,
    }
)


async def _validate_cloud_credentials(
    username: str,
    password: str,
    region: str,
    timeout: int,
) -> tuple[str, str]:
    """Validate Hik-Connect credentials and resolve the effective base URL.

    Args:
        username: Account username.
        password: Account password.
        region: Region key ('EU', 'Asia', 'USA').
        timeout: HTTP timeout.

    Returns:
        Tuple of (resolved_base_url, resolved_region).

    Raises:
        AuthError: On wrong credentials.
        CaptchaRequired: If CAPTCHA is needed.
        HikvisionEyError: On other API errors.
        aiohttp.ClientError: On network errors.
    """
    async with HikvisionEyClient(region=region, timeout=timeout) as client:
        await client.login(username, password)
        # login() aggiorna self._base_url se c'è stato redirect
        return client.base_url, region


class HikvisionEyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the configuration flow for Hikvision EY."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step — cloud credentials.

        Args:
            user_input: Data submitted by the user.

        Returns:
            FlowResult directing to next step or showing form with errors.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            region = user_input[CONF_REGION]
            timeout = user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

            try:
                base_url, _ = await _validate_cloud_credentials(username, password, region, timeout)
            except CaptchaRequired:
                _LOGGER.warning("[ConfigFlow] CAPTCHA required for %s", username)
                errors["base"] = "captcha_required"
            except AuthError:
                _LOGGER.warning("[ConfigFlow] Auth failed for %s", username)
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, OSError):
                _LOGGER.exception("[ConfigFlow] Network error during validation")
                errors["base"] = "cannot_connect"
            except HikvisionEyError:
                _LOGGER.exception("[ConfigFlow] API error during validation")
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("[ConfigFlow] Unexpected error during validation")
                errors["base"] = "unknown"
            else:
                # Imposta unique_id = username per evitare duplicati
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()

                entry_data = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_REGION: region,
                    CONF_TIMEOUT: timeout,
                    "base_url": base_url,  # URL risolto dopo eventuale redirect
                }
                _LOGGER.info("[ConfigFlow] Creating entry for user %s (region=%s)", username, region)
                return self.async_create_entry(title=username, data=entry_data)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthentication when session expires.

        Args:
            user_input: Existing entry data passed by HA.

        Returns:
            FlowResult directing to reauth_confirm step.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the reauth confirmation form.

        Args:
            user_input: New credentials submitted by user.

        Returns:
            FlowResult showing form or completing reauth.
        """
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None  # noqa: S101

        existing_data = self._reauth_entry.data

        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_REGION, default=existing_data.get(CONF_REGION, DEFAULT_REGION)): vol.In(REGION_LIST),
            }
        )

        if user_input is not None:
            try:
                base_url, _ = await _validate_cloud_credentials(
                    existing_data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input.get(CONF_REGION, existing_data.get(CONF_REGION, DEFAULT_REGION)),
                    existing_data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                )
            except CaptchaRequired:
                errors["base"] = "captcha_required"
            except AuthError:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("[ConfigFlow] Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                new_data = {
                    **existing_data,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REGION: user_input.get(CONF_REGION, existing_data.get(CONF_REGION, DEFAULT_REGION)),
                    "base_url": base_url,
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=new_data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HikvisionEyOptionsFlow:
        """Create the options flow handler."""
        return HikvisionEyOptionsFlow(config_entry)


class HikvisionEyOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow for Hikvision EY.

    Permette configurazione opzionale di:
    - Timeout
    - ISAPI locale (host, username, password)
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry
        self._local_enabled: bool = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options init step (timeout + local ISAPI toggle).

        Args:
            user_input: User submitted options.

        Returns:
            FlowResult — either proceeds to local_isapi step or saves.
        """
        current_options = self._config_entry.options

        if user_input is not None:
            self._local_enabled = user_input.get(CONF_LOCAL_ISAPI_ENABLED, False)

            if self._local_enabled:
                # Vai al passo di configurazione ISAPI locale
                return await self.async_step_local_isapi()

            # Salva opzioni senza ISAPI locale
            return self.async_create_entry(
                title="",
                data={
                    CONF_TIMEOUT: user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    CONF_LOCAL_ISAPI_ENABLED: False,
                },
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_LOCAL_ISAPI_ENABLED,
                    default=current_options.get(CONF_LOCAL_ISAPI_ENABLED, False),
                ): bool,
                vol.Optional(
                    CONF_TIMEOUT,
                    default=current_options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                ): vol.All(int, vol.Range(min=5, max=120)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_local_isapi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the optional Local ISAPI configuration step.

        Args:
            user_input: Local ISAPI settings.

        Returns:
            FlowResult saving options or showing form with errors.
        """
        errors: dict[str, str] = {}
        current_options = self._config_entry.options

        if user_input is not None:
            # Test di connettività opzionale
            host = user_input[CONF_LOCAL_HOST]
            local_user = user_input[CONF_LOCAL_USERNAME]
            local_pass = user_input[CONF_LOCAL_PASSWORD]

            timeout = current_options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)

            try:
                from .api.isapi import LocalISAPIClient

                async with LocalISAPIClient(host, local_user, local_pass, timeout) as isapi:
                    result = await isapi.test_connectivity()

                if not result.get("reachable"):
                    _LOGGER.warning(
                        "[OptionsFlow] Local ISAPI not reachable at %s: %s",
                        host,
                        result.get("error"),
                    )
                    errors["base"] = "cannot_connect_local"
                else:
                    device_name = result.get("device_name") or host
                    _LOGGER.info("[OptionsFlow] Local ISAPI OK — device: %s", device_name)

            except Exception:  # noqa: BLE001
                _LOGGER.exception("[OptionsFlow] Local ISAPI test failed")
                errors["base"] = "cannot_connect_local"

            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_TIMEOUT: current_options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                        CONF_LOCAL_ISAPI_ENABLED: True,
                        CONF_LOCAL_HOST: host,
                        CONF_LOCAL_USERNAME: local_user,
                        CONF_LOCAL_PASSWORD: local_pass,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LOCAL_HOST,
                    default=current_options.get(CONF_LOCAL_HOST, ""),
                ): str,
                vol.Required(
                    CONF_LOCAL_USERNAME,
                    default=current_options.get(CONF_LOCAL_USERNAME, "admin"),
                ): str,
                vol.Required(CONF_LOCAL_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="local_isapi",
            data_schema=schema,
            errors=errors,
        )
