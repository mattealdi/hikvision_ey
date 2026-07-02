"""Test per HikvisionEyClient — login, refresh, JWT decode, region redirect."""
from __future__ import annotations

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hikvision_ey.api.client import HikvisionEyClient, _mask_token
from custom_components.hikvision_ey.api.exceptions import (
    AuthError,
    CaptchaRequired,
    HikvisionEyError,
)
from tests.conftest import (
    MOCK_JWT,
    MOCK_PASSWORD,
    MOCK_REFRESH_TOKEN,
    MOCK_USERNAME,
    make_cameras_response,
    make_call_status_response,
    make_device_pagelist_response,
    make_login_error_response,
    make_login_response,
    make_refresh_response,
    make_region_redirect_response,
)


# ── _mask_token ────────────────────────────────────────────────────────────────


def test_mask_token_short() -> None:
    """Token corto deve restituire ***."""
    assert _mask_token("abc") == "***"


def test_mask_token_normal() -> None:
    """Token lungo deve mostrare primi 3 e ultimi 3."""
    result = _mask_token("abcdefghij")
    assert result == "abc...hij"
    assert "defg" not in result


# ── JWT decode ─────────────────────────────────────────────────────────────────


def test_decode_jwt_expiration() -> None:
    """Il JWT con exp=4070908800 (anno 2099) deve darci una data futura."""
    # JWT con payload {"sub": "test", "exp": 4070908800}
    result = HikvisionEyClient._decode_jwt_expiration(MOCK_JWT)
    assert isinstance(result, datetime.datetime)
    assert result.year > 2025


def test_decode_jwt_invalid() -> None:
    """JWT non valido deve restituire data nel passato (per forzare refresh)."""
    result = HikvisionEyClient._decode_jwt_expiration("not.a.jwt")
    # Deve essere "ora" circa (passato o presente)
    assert isinstance(result, datetime.datetime)


# ── Login ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_success() -> None:
    """Login corretto deve impostare session_id e login_valid_until."""
    client = HikvisionEyClient(region="EU")
    login_resp = make_login_response(session_id=MOCK_JWT, rf_session_id=MOCK_REFRESH_TOKEN)

    with patch.object(client, "_request", AsyncMock(return_value=login_resp)):
        await client.login(MOCK_USERNAME, MOCK_PASSWORD)

    assert client._session_id == MOCK_JWT
    assert client._refresh_session_id == MOCK_REFRESH_TOKEN
    assert client.login_valid_until is not None
    assert client.login_valid_until.year > 2025
    await client.close()


@pytest.mark.asyncio
async def test_login_wrong_password() -> None:
    """Login con password errata deve sollevare AuthError."""
    client = HikvisionEyClient(region="EU")
    error_resp = make_login_error_response(1013, "Wrong password")

    with patch.object(client, "_request", AsyncMock(return_value=error_resp)):
        with pytest.raises(AuthError):
            await client.login(MOCK_USERNAME, "wrong_password")
    await client.close()


@pytest.mark.asyncio
async def test_login_captcha_required() -> None:
    """Login con CAPTCHA richiesto deve sollevare CaptchaRequired."""
    client = HikvisionEyClient(region="EU")
    captcha_resp = make_login_error_response(1015, "Captcha required")

    with patch.object(client, "_request", AsyncMock(return_value=captcha_resp)):
        with pytest.raises(CaptchaRequired):
            await client.login(MOCK_USERNAME, MOCK_PASSWORD)
    await client.close()


@pytest.mark.asyncio
async def test_login_region_redirect() -> None:
    """Login con redirect regione (1100) deve aggiornare base_url e riprovare."""
    client = HikvisionEyClient(region="Asia")
    redirect_resp = make_region_redirect_response("apiieu.hik-connect.com")
    success_resp = make_login_response(session_id=MOCK_JWT)

    call_count = 0

    async def mock_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return redirect_resp
        return success_resp

    with patch.object(client, "_request", AsyncMock(side_effect=mock_request)):
        await client.login(MOCK_USERNAME, MOCK_PASSWORD)

    assert "apiieu.hik-connect.com" in client.base_url
    assert call_count == 2
    await client.close()


# ── Refresh ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_login_success() -> None:
    """Refresh con refreshSessionId valido deve aggiornare il session_id."""
    client = HikvisionEyClient(region="EU")
    client._refresh_session_id = MOCK_REFRESH_TOKEN
    refresh_resp = make_refresh_response(session_id=MOCK_JWT)

    with patch.object(client, "_request", AsyncMock(return_value=refresh_resp)):
        await client.refresh_login()

    assert client._session_id == MOCK_JWT
    await client.close()


@pytest.mark.asyncio
async def test_refresh_login_no_token() -> None:
    """Refresh senza refreshSessionId deve sollevare AuthError."""
    client = HikvisionEyClient(region="EU")
    client._refresh_session_id = None

    with pytest.raises(AuthError):
        await client.refresh_login()
    await client.close()


# ── needs_token_refresh ────────────────────────────────────────────────────────


def test_needs_refresh_when_no_token() -> None:
    """Senza login_valid_until, il token deve essere considerato scaduto."""
    client = HikvisionEyClient(region="EU")
    assert client.needs_token_refresh() is True


def test_needs_refresh_when_expires_soon() -> None:
    """Token che scade in 30 minuti deve richiedere refresh."""
    client = HikvisionEyClient(region="EU")
    client.login_valid_until = datetime.datetime.now() + datetime.timedelta(minutes=30)
    assert client.needs_token_refresh() is True


def test_no_refresh_when_valid() -> None:
    """Token che scade tra 5 ore non deve richiedere refresh."""
    client = HikvisionEyClient(region="EU")
    client.login_valid_until = datetime.datetime.now() + datetime.timedelta(hours=5)
    assert client.needs_token_refresh() is False


# ── get_devices ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_devices() -> None:
    """get_devices deve restituire lista DeviceInfo parsata."""
    client = HikvisionEyClient(region="EU")
    devices_resp = make_device_pagelist_response()
    cameras_resp = make_cameras_response()

    request_responses = [devices_resp, cameras_resp]
    call_idx = 0

    async def mock_request(*args, **kwargs):
        nonlocal call_idx
        resp = request_responses[call_idx % len(request_responses)]
        call_idx += 1
        return resp

    with patch.object(client, "_request", AsyncMock(side_effect=mock_request)):
        devices = await client.get_devices()

    assert len(devices) == 1
    assert devices[0].serial == "K12345678"
    assert devices[0].is_online is True
    assert devices[0].wifi_signal == -65
    await client.close()


# ── get_call_status ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_call_status_idle() -> None:
    """get_call_status deve parsare correttamente status idle."""
    client = HikvisionEyClient(region="EU")
    status_resp = make_call_status_response(1)

    with patch.object(client, "_request", AsyncMock(return_value=status_resp)):
        status = await client.get_call_status("K12345678")

    assert status.status == "idle"
    assert status.is_ringing is False
    await client.close()


@pytest.mark.asyncio
async def test_get_call_status_ringing() -> None:
    """get_call_status deve parsare correttamente status ringing."""
    from custom_components.hikvision_ey.api.exceptions import DeviceOffline

    client = HikvisionEyClient(region="EU")
    status_resp = make_call_status_response(2)

    with patch.object(client, "_request", AsyncMock(return_value=status_resp)):
        status = await client.get_call_status("K12345678")

    assert status.status == "ringing"
    assert status.is_ringing is True
    await client.close()


@pytest.mark.asyncio
async def test_get_call_status_device_offline() -> None:
    """get_call_status con meta.code=2003 deve sollevare DeviceOffline."""
    from custom_components.hikvision_ey.api.exceptions import DeviceOffline

    client = HikvisionEyClient(region="EU")
    offline_resp = {"data": "{}", "meta": {"code": 2003}}

    with patch.object(client, "_request", AsyncMock(return_value=offline_resp)):
        with pytest.raises(DeviceOffline):
            await client.get_call_status("K12345678")
    await client.close()
