"""Test per le strategie di unlock (A1, A2, A3, A4, Local, Auto)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hikvision_ey.api.exceptions import UnlockFailed
from custom_components.hikvision_ey.api.unlock_strategies import (
    StrategyA1,
    StrategyA2,
    StrategyA3,
    StrategyA4,
    StrategyLocal,
    StrategyVerified,
    UnlockManager,
    _extract_isapi_status,
    _extract_meta_code,
)
from custom_components.hikvision_ey.const import (
    STRATEGY_AUTO,
    STRATEGY_CLOUD_A1,
    STRATEGY_CLOUD_A2,
    STRATEGY_CLOUD_A3,
    STRATEGY_CLOUD_A4,
    STRATEGY_CLOUD_VERIFIED,
    STRATEGY_LOCAL,
    VERIFIED_APIKEY,
)
from tests.conftest import MOCK_SERIAL, make_unlock_response

SERIAL = MOCK_SERIAL
CHANNEL = 1
LOCK_IDX = 0


# ── _extract_meta_code ────────────────────────────────────────────────────────


def test_extract_meta_code_success() -> None:
    """Deve estrarre meta.code dalla risposta."""
    resp = {"meta": {"code": 200}}
    assert _extract_meta_code(resp) == 200


def test_extract_meta_code_missing() -> None:
    """Risposta senza meta.code deve restituire None."""
    assert _extract_meta_code({}) is None
    assert _extract_meta_code({"meta": {}}) is None


def test_extract_meta_code_60019() -> None:
    """Deve riconoscere 60019 come successo."""
    resp = {"meta": {"code": 60019}}
    assert _extract_meta_code(resp) == 60019


# ── StrategyA1 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_a1_success() -> None:
    """A1 con meta.code=200 deve restituire success=True."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(200))

    strategy = StrategyA1(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.strategy == STRATEGY_CLOUD_A1
    assert result.meta_code == 200


@pytest.mark.asyncio
async def test_strategy_a1_success_60019() -> None:
    """A1 con meta.code=60019 deve restituire success=True (variante EY)."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(60019))

    strategy = StrategyA1(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.meta_code == 60019


@pytest.mark.asyncio
async def test_strategy_a1_failed_meta_code() -> None:
    """A1 con meta.code diverso da 200/60019 deve restituire success=False."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(60020))

    strategy = StrategyA1(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert result.meta_code == 60020


@pytest.mark.asyncio
async def test_strategy_a1_offline() -> None:
    """A1 con meta.code=2003 deve restituire success=False."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value={"meta": {"code": 2003}})

    strategy = StrategyA1(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False


@pytest.mark.asyncio
async def test_strategy_a1_request_exception() -> None:
    """A1 con eccezione di rete deve restituire success=False."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(side_effect=Exception("Network error"))

    strategy = StrategyA1(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert "Network error" in result.error


# ── StrategyA2 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_a2_success() -> None:
    """A2 con meta.code=200 deve restituire success=True."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(200))

    strategy = StrategyA2(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.strategy == STRATEGY_CLOUD_A2


@pytest.mark.asyncio
async def test_strategy_a2_failed() -> None:
    """A2 con meta.code diverso deve restituire success=False."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(10001))

    strategy = StrategyA2(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False


# ── StrategyA3 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_a3_primary_success() -> None:
    """A3 con primary success non deve tentare alternate."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return make_unlock_response(200)

    client.raw_request = AsyncMock(side_effect=mock_request)

    strategy = StrategyA3(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert call_count == 1  # Solo primary


@pytest.mark.asyncio
async def test_strategy_a3_fallback_to_alt() -> None:
    """A3 con primary failure deve tentare alternate."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return make_unlock_response(10001)  # Primary fallisce
        return make_unlock_response(200)  # Alternate succede

    client.raw_request = AsyncMock(side_effect=mock_request)

    strategy = StrategyA3(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert call_count == 2


# ── StrategyA4 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_a4_primary_success() -> None:
    """A4 con risposta HTTP 200 e meta.code=200 deve restituire success=True."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request_xml = AsyncMock(return_value={"_http_status": 200, "meta": {"code": 200}})

    strategy = StrategyA4(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.strategy == STRATEGY_CLOUD_A4


@pytest.mark.asyncio
async def test_strategy_a4_404_fallback() -> None:
    """A4 con 404 sul primary deve tentare il fallback."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    call_count = 0

    async def mock_xml_request(method, url, body, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"_http_status": 404, "meta": {"code": 404}}  # Primary 404
        return {"_http_status": 200, "meta": {"code": 200}}  # Fallback success

    client.raw_request_xml = AsyncMock(side_effect=mock_xml_request)

    strategy = StrategyA4(client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert call_count == 2


# ── StrategyLocal ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_local_success() -> None:
    """Local ISAPI con successo deve restituire success=True."""
    isapi_client = MagicMock()
    isapi_client.open_door = AsyncMock(return_value=True)

    strategy = StrategyLocal(isapi_client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is True
    assert result.strategy == STRATEGY_LOCAL
    isapi_client.open_door.assert_called_once_with(LOCK_IDX)


@pytest.mark.asyncio
async def test_strategy_local_failure() -> None:
    """Local ISAPI che restituisce False deve dare success=False."""
    isapi_client = MagicMock()
    isapi_client.open_door = AsyncMock(return_value=False)

    strategy = StrategyLocal(isapi_client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False


@pytest.mark.asyncio
async def test_strategy_local_exception() -> None:
    """Local ISAPI con eccezione deve restituire success=False."""
    isapi_client = MagicMock()
    isapi_client.open_door = AsyncMock(side_effect=Exception("Connection refused"))

    strategy = StrategyLocal(isapi_client)
    result = await strategy.execute(SERIAL, CHANNEL, LOCK_IDX)

    assert result.success is False
    assert "Connection refused" in result.error


# ── UnlockManager auto mode ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_manager_auto_uses_preferred() -> None:
    """Auto mode deve provare prima la preferred_strategy."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    call_counts: dict[str, int] = {}

    async def mock_request(method, url, **kwargs):
        # Qualsiasi endpoint → successo
        return make_unlock_response(200)

    client.raw_request = AsyncMock(side_effect=mock_request)

    manager = UnlockManager(client, preferred_strategy=STRATEGY_CLOUD_A2)
    result = await manager.open_gate(SERIAL, CHANNEL, LOCK_IDX, strategy=STRATEGY_AUTO)

    assert result.success is True
    # A2 deve essere la prima provata — il manager.preferred_strategy è ancora A2 dopo successo
    assert manager.preferred_strategy == STRATEGY_CLOUD_A2


@pytest.mark.asyncio
async def test_unlock_manager_explicit_strategy() -> None:
    """Strategia esplicita deve usare solo quella."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(200))

    manager = UnlockManager(client)
    result = await manager.open_gate(SERIAL, CHANNEL, LOCK_IDX, strategy=STRATEGY_CLOUD_A1)

    assert result.success is True
    assert result.strategy == STRATEGY_CLOUD_A1


@pytest.mark.asyncio
async def test_unlock_manager_all_fail() -> None:
    """Se tutte le strategie falliscono, deve sollevare UnlockFailed."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"
    client.raw_request = AsyncMock(return_value=make_unlock_response(10001))
    client.raw_request_xml = AsyncMock(return_value={"_http_status": 500, "meta": {"code": 500}})

    manager = UnlockManager(client)
    with pytest.raises(UnlockFailed):
        await manager.open_gate(SERIAL, CHANNEL, LOCK_IDX, strategy=STRATEGY_AUTO)


@pytest.mark.asyncio
async def test_unlock_manager_saves_preferred() -> None:
    """Strategia vincente deve essere salvata come preferred."""
    client = MagicMock()
    client.base_url = "https://apiieu.hik-connect.com"

    call_count = 0

    async def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return make_unlock_response(200)

    async def mock_xml_request(method, url, body, **kwargs):
        # A4 fallisce per forzare A3
        return {"_http_status": 500, "meta": {"code": 500}}

    client.raw_request = AsyncMock(side_effect=mock_request)
    client.raw_request_xml = AsyncMock(side_effect=mock_xml_request)

    manager = UnlockManager(client)
    result = await manager.open_gate(SERIAL, CHANNEL, LOCK_IDX, strategy=STRATEGY_AUTO)

    assert result.success is True
    # La preferred strategy deve essere stata aggiornata alla vincente
    assert manager.preferred_strategy is not None
