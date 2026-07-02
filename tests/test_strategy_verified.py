"""Test dedicati alla strategia cloud_verified (endpoint ISAPI tunnel).

Verifica che l'implementazione replichi esattamente la request osservata sul
device reale (DS-KV7413EY-IME2) e parsi correttamente la response ISAPI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hikvision_ey.api.unlock_strategies import (
    StrategyVerified,
    _extract_isapi_status,
)
from custom_components.hikvision_ey.const import (
    STRATEGY_CLOUD_VERIFIED,
    VERIFIED_APIKEY,
)
from tests.conftest import MOCK_SERIAL

SERIAL = MOCK_SERIAL
CHANNEL = 1
LOCK_IDX = 0

# Payload reale del cancelletto pedonale — catturato via Reqable + mitmproxy
# sull'app HikConnect iOS in data 02/07/2026.
_REAL_ISAPI_SUCCESS_DATA = (
    '<?xml version="1.0" encoding="UTF-8" ?>\n'
    '<ResponseStatus version="1.0" xmlns="urn:psialliance-org">\n'
    "<requestURL>/ISAPI/AccessControl/RemoteControl/door/1</requestURL>\n"
    "<statusCode>1</statusCode>\n"
    "<statusString>OK</statusString>\n"
    "<subStatusCode>ok</subStatusCode>\n"
    "<errorCode>1</errorCode>\n"
    "<errorMsg>0x1</errorMsg>\n"
    "</ResponseStatus>\n"
)


def _make_verified_success_response() -> dict:
    """Response completa che replica quella reale del device."""
    return {
        "data": _REAL_ISAPI_SUCCESS_DATA,
        "meta": {"code": 200, "message": "OK"},
        "_http_status": 200,
    }


# ---- _extract_isapi_status -------------------------------------------------


def test_extract_isapi_status_real_success_payload() -> None:
    """Payload reale del cancelletto pedonale: statusCode=1, subStatus=ok."""
    resp = {"data": _REAL_ISAPI_SUCCESS_DATA, "meta": {"code": 200}}
    status, sub = _extract_isapi_status(resp)
    assert status == 1
    assert sub == "ok"


def test_extract_isapi_status_missing() -> None:
    """Se `data` non c'è o non è stringa deve restituire (None, None)."""
    assert _extract_isapi_status({}) == (None, None)
    assert _extract_isapi_status({"data": None}) == (None, None)
    assert _extract_isapi_status({"data": 123}) == (None, None)


def test_extract_isapi_status_partial_payload() -> None:
    """Con solo statusCode ma senza subStatus deve estrarre parziale."""
    resp = {"data": "<ResponseStatus><statusCode>0</statusCode></ResponseStatus>"}
    status, sub = _extract_isapi_status(resp)
    assert status == 0
    assert sub is None


def test_extract_isapi_status_error_code() -> None:
    """statusCode negativo (-1) deve essere estratto correttamente."""
    data = "<ResponseStatus><statusCode>-1</statusCode><subStatusCode>err</subStatusCode></ResponseStatus>"
    status, sub = _extract_isapi_status({"data": data})
    assert status == -1
    assert sub == "err"


# ---- StrategyVerified -----------------------------------------------------


@pytest.mark.asyncio
async def test_strategy_verified_success_real_payload() -> None:
    """StrategyVerified deve confermare unlock con la response reale."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(return_value=_make_verified_success_response())

    strategy = StrategyVerified(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.strategy == STRATEGY_CLOUD_VERIFIED
    assert result.meta_code == 200
    assert result.http_status == 200


@pytest.mark.asyncio
async def test_strategy_verified_sends_exact_form_fields() -> None:
    """Il form-data inviato deve replicare ESATTAMENTE quello dell'app."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(return_value=_make_verified_success_response())

    strategy = StrategyVerified(client)
    await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    call = client.raw_request_form.await_args
    method, url, form = call.args[0], call.args[1], call.args[2]

    assert method == "POST"
    assert url.endswith("/v3/userdevices/v1/isapi")
    assert form["apiKey"] == VERIFIED_APIKEY == "100044"
    assert form["apiData"] == "PUT /ISAPI/AccessControl/RemoteControl/door/1"
    assert form["channelNo"] == "1"
    assert form["deviceSerial"] == SERIAL
    assert form["method"] == "0"


@pytest.mark.asyncio
async def test_strategy_verified_lock_index_2_maps_to_door_2() -> None:
    """lock_index=1 deve tradursi in door/2 (1-based)."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(return_value=_make_verified_success_response())

    strategy = StrategyVerified(client)
    await strategy.execute(SERIAL, CHANNEL, lock_index=1)

    form = client.raw_request_form.await_args.args[2]
    assert form["apiData"] == "PUT /ISAPI/AccessControl/RemoteControl/door/2"


@pytest.mark.asyncio
async def test_strategy_verified_failure_isapi_error() -> None:
    """Cloud 200 ma ISAPI statusCode=-1 deve restituire success=False."""
    error_data = (
        "<ResponseStatus><statusCode>-1</statusCode>"
        "<statusString>Device Error</statusString>"
        "<subStatusCode>deviceError</subStatusCode></ResponseStatus>"
    )
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(
        return_value={
            "data": error_data,
            "meta": {"code": 200},
            "_http_status": 200,
        }
    )

    strategy = StrategyVerified(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert result.strategy == STRATEGY_CLOUD_VERIFIED
    assert "deviceError" in (result.error or "")


@pytest.mark.asyncio
async def test_strategy_verified_failure_cloud_error() -> None:
    """meta.code != 200 deve restituire success=False anche se ISAPI OK."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(
        return_value={
            "data": _REAL_ISAPI_SUCCESS_DATA,
            "meta": {"code": 10001},  # errore auth simulato
            "_http_status": 200,
        }
    )

    strategy = StrategyVerified(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert result.meta_code == 10001


@pytest.mark.asyncio
async def test_strategy_verified_request_exception() -> None:
    """Un'eccezione di rete deve dare success=False con error settato."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(side_effect=RuntimeError("connection lost"))

    strategy = StrategyVerified(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert result.strategy == STRATEGY_CLOUD_VERIFIED
    assert "connection lost" in (result.error or "")


@pytest.mark.asyncio
async def test_strategy_verified_fail_open_when_isapi_unparseable() -> None:
    """Se meta.code=200 ma la data XML non è parsabile, deve considerare successo.

    Comportamento fail-open per gestire eventuali variazioni di formato lato
    server: se il cloud dice OK e non c'è statusCode ISAPI, non blocchiamo.
    """
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_form = AsyncMock(
        return_value={
            "data": "some non-xml payload",
            "meta": {"code": 200},
            "_http_status": 200,
        }
    )

    strategy = StrategyVerified(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
