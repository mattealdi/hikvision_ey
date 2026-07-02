"""Test per il config flow e l'options flow di Hikvision EY."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hikvision_ey.api.exceptions import AuthError, CaptchaRequired
from custom_components.hikvision_ey.const import (
    CONF_PASSWORD,
    CONF_REGION,
    CONF_TIMEOUT,
    CONF_USERNAME,
    DOMAIN,
)
from tests.conftest import (
    MOCK_BASE_URL,
    MOCK_PASSWORD,
    MOCK_REGION,
    MOCK_USERNAME,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_flow_manager_mock(hass):
    """Restituisce un mock per config_entries simile a HA."""
    hass.config_entries = MagicMock()
    hass.config_entries.flow = MagicMock()
    return hass


# ── Test config flow ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_flow_happy_path(mock_hass) -> None:
    """Happy path: credenziali corrette → entry creata."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    flow.async_create_entry = MagicMock(return_value={"type": FlowResultType.CREATE_ENTRY})

    user_input = {
        CONF_USERNAME: MOCK_USERNAME,
        CONF_PASSWORD: MOCK_PASSWORD,
        CONF_REGION: MOCK_REGION,
        CONF_TIMEOUT: 15,
    }

    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(return_value=(MOCK_BASE_URL, MOCK_REGION)),
    ):
        result = await flow.async_step_user(user_input)

    flow.async_create_entry.assert_called_once()
    call_kwargs = flow.async_create_entry.call_args
    assert call_kwargs[1]["data"][CONF_USERNAME] == MOCK_USERNAME


@pytest.mark.asyncio
async def test_config_flow_auth_error(mock_hass) -> None:
    """Credenziali errate → errore invalid_auth nella form."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow.async_show_form = MagicMock(return_value={"type": FlowResultType.FORM})

    user_input = {
        CONF_USERNAME: MOCK_USERNAME,
        CONF_PASSWORD: "wrong_password",
        CONF_REGION: MOCK_REGION,
        CONF_TIMEOUT: 15,
    }

    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(side_effect=AuthError("Wrong credentials")),
    ):
        result = await flow.async_step_user(user_input)

    flow.async_show_form.assert_called_once()
    call_kwargs = flow.async_show_form.call_args
    errors = call_kwargs[1].get("errors", {})
    assert "base" in errors
    assert errors["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_config_flow_captcha_required(mock_hass) -> None:
    """CAPTCHA richiesto → errore captcha_required nella form."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow.async_show_form = MagicMock(return_value={"type": FlowResultType.FORM})

    user_input = {
        CONF_USERNAME: MOCK_USERNAME,
        CONF_PASSWORD: MOCK_PASSWORD,
        CONF_REGION: MOCK_REGION,
        CONF_TIMEOUT: 15,
    }

    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(side_effect=CaptchaRequired("CAPTCHA")),
    ):
        result = await flow.async_step_user(user_input)

    flow.async_show_form.assert_called_once()
    call_kwargs = flow.async_show_form.call_args
    errors = call_kwargs[1].get("errors", {})
    assert errors.get("base") == "captcha_required"


@pytest.mark.asyncio
async def test_config_flow_cannot_connect(mock_hass) -> None:
    """Errore di rete → cannot_connect nella form."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow.async_show_form = MagicMock(return_value={"type": FlowResultType.FORM})

    user_input = {
        CONF_USERNAME: MOCK_USERNAME,
        CONF_PASSWORD: MOCK_PASSWORD,
        CONF_REGION: MOCK_REGION,
        CONF_TIMEOUT: 15,
    }

    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(side_effect=aiohttp.ClientError("Connection refused")),
    ):
        result = await flow.async_step_user(user_input)

    flow.async_show_form.assert_called_once()
    call_kwargs = flow.async_show_form.call_args
    errors = call_kwargs[1].get("errors", {})
    assert errors.get("base") == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_region_redirect_handled(mock_hass) -> None:
    """Region redirect deve essere gestito internamente e creare entry con nuovo URL."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    flow.async_create_entry = MagicMock(return_value={"type": FlowResultType.CREATE_ENTRY})

    user_input = {
        CONF_USERNAME: MOCK_USERNAME,
        CONF_PASSWORD: MOCK_PASSWORD,
        CONF_REGION: "Asia",
        CONF_TIMEOUT: 15,
    }

    # Simula redirect: validate restituisce URL EU
    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(return_value=("https://apiieu.hik-connect.com", "Asia")),
    ):
        result = await flow.async_step_user(user_input)

    flow.async_create_entry.assert_called_once()
    call_kwargs = flow.async_create_entry.call_args
    assert "apiieu.hik-connect.com" in call_kwargs[1]["data"]["base_url"]


@pytest.mark.asyncio
async def test_config_flow_show_form_on_first_load(mock_hass) -> None:
    """Senza user_input deve mostrare il form."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {}
    flow.async_show_form = MagicMock(return_value={"type": FlowResultType.FORM})

    result = await flow.async_step_user(None)

    flow.async_show_form.assert_called_once()
    call_kwargs = flow.async_show_form.call_args
    assert call_kwargs[1]["step_id"] == "user"


# ── Test reauth flow ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reauth_success(mock_hass, mock_config_entry) -> None:
    """Reauth con nuova password valida deve aggiornare l'entry."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyConfigFlow

    flow = HikvisionEyConfigFlow()
    flow.hass = mock_hass
    flow.context = {"entry_id": mock_config_entry.entry_id}
    flow._reauth_entry = mock_config_entry
    mock_hass.config_entries.async_get_entry = MagicMock(return_value=mock_config_entry)
    mock_hass.config_entries.async_update_entry = MagicMock()
    mock_hass.config_entries.async_reload = AsyncMock()
    flow.async_abort = MagicMock(return_value={"type": FlowResultType.ABORT})

    user_input = {
        CONF_PASSWORD: "NewPassword456",
        CONF_REGION: MOCK_REGION,
    }

    with patch(
        "custom_components.hikvision_ey.config_flow._validate_cloud_credentials",
        AsyncMock(return_value=(MOCK_BASE_URL, MOCK_REGION)),
    ):
        result = await flow.async_step_reauth_confirm(user_input)

    flow.async_abort.assert_called_once_with(reason="reauth_successful")


# ── Test options flow ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_options_flow_no_local(mock_config_entry) -> None:
    """Options flow senza ISAPI locale deve salvare solo il timeout."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyOptionsFlow

    flow = HikvisionEyOptionsFlow(mock_config_entry)
    flow.async_create_entry = MagicMock(return_value={"type": FlowResultType.CREATE_ENTRY})

    user_input = {
        "local_isapi_enabled": False,
        "timeout": 20,
    }

    result = await flow.async_step_init(user_input)

    flow.async_create_entry.assert_called_once()
    data = flow.async_create_entry.call_args[1]["data"]
    assert data["local_isapi_enabled"] is False
    assert data["timeout"] == 20


@pytest.mark.asyncio
async def test_options_flow_local_isapi_reachable(mock_config_entry) -> None:
    """Options flow con ISAPI locale raggiungibile deve salvare la config."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyOptionsFlow

    flow = HikvisionEyOptionsFlow(mock_config_entry)
    flow._local_enabled = True
    flow.async_create_entry = MagicMock(return_value={"type": FlowResultType.CREATE_ENTRY})

    user_input = {
        "local_host": "192.168.1.100",
        "local_username": "admin",
        "local_password": "TestPass123",
    }

    with patch(
        "custom_components.hikvision_ey.config_flow.LocalISAPIClient",
        MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock(
                    test_connectivity=AsyncMock(return_value={"reachable": True, "device_name": "Monitor"})
                )),
                __aexit__=AsyncMock(return_value=None),
            )
        ),
    ):
        result = await flow.async_step_local_isapi(user_input)

    flow.async_create_entry.assert_called_once()
    data = flow.async_create_entry.call_args[1]["data"]
    assert data["local_isapi_enabled"] is True
    assert data["local_host"] == "192.168.1.100"


@pytest.mark.asyncio
async def test_options_flow_local_isapi_unreachable(mock_config_entry) -> None:
    """Options flow con ISAPI non raggiungibile deve mostrare errore."""
    from custom_components.hikvision_ey.config_flow import HikvisionEyOptionsFlow

    flow = HikvisionEyOptionsFlow(mock_config_entry)
    flow._local_enabled = True
    flow.async_show_form = MagicMock(return_value={"type": FlowResultType.FORM})

    user_input = {
        "local_host": "192.168.99.99",
        "local_username": "admin",
        "local_password": "WrongPass",
    }

    with patch(
        "custom_components.hikvision_ey.config_flow.LocalISAPIClient",
        MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock(
                    test_connectivity=AsyncMock(return_value={"reachable": False, "error": "Timeout"})
                )),
                __aexit__=AsyncMock(return_value=None),
            )
        ),
    ):
        result = await flow.async_step_local_isapi(user_input)

    flow.async_show_form.assert_called_once()
    call_kwargs = flow.async_show_form.call_args
    errors = call_kwargs[1].get("errors", {})
    assert errors.get("base") == "cannot_connect_local"
