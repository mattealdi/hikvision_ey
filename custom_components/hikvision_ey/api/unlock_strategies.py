"""Strategie di unlock per Hikvision EY.

Implementa 5 strategie cloud, con logica auto-selection e logging dettagliato.

- cloud_verified (A0): ISAPI tunnel via /v3/userdevices/v1/isapi
  con body form-urlencoded. **Verificato empiricamente** su
  DS-KV7413EY-IME2 + DS-KH7300EY-WTE2 + DS-KAD7063 (luglio 2026,
  reverse-engineering via mitmproxy dell'app HikConnect iOS).
- cloud_a1..a4: strategie legacy/sperimentali mantenute come fallback
  per compatibilità con altre configurazioni hardware.

NOTA v0.3.6: la strategia `local` (ISAPI diretto LAN) è stata rimossa
dalle strategie attive dopo verifica sperimentale del 3/7/2026 sul
DS-KH7300EY-WTE2 SN GA9672303: il monitor serie EY consumer risponde
al ping ma tiene chiuse TUTTE le porte TCP di gestione (80, 443, 554,
8000, 8080, 8200) e non risponde al broadcast SADP. Il canale ISAPI
locale non è raggiungibile end-user su questa gamma di prodotto.
StrategyLocal è stata rimossa per non generare log fuorvianti.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Protocol

from ..const import (
    ISAPI_SUCCESS_STATUS_CODES,
    STRATEGY_AUTO,
    STRATEGY_CLOUD_A1,
    STRATEGY_CLOUD_A2,
    STRATEGY_CLOUD_A3,
    STRATEGY_CLOUD_A4,
    STRATEGY_CLOUD_VERIFIED,
    STRATEGY_LOCAL,
    UNLOCK_SUCCESS_CODES,
    VERIFIED_APIDATA,
    VERIFIED_APIKEY,
    VERIFIED_CHANNEL_NO,
    VERIFIED_METHOD,
)
from . import endpoints
from .exceptions import UnlockFailed
from .models import UnlockResult

if TYPE_CHECKING:
    from .client import HikvisionEyClient
    from .isapi import LocalISAPIClient

_LOGGER = logging.getLogger(__name__)

# XML body per strategia A4 legacy (non più primaria)
_ISAPI_DOOR_OPEN_XML = b"<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>"

# Regex per estrarre statusCode dall'XML ResponseStatus (payload ISAPI)
_ISAPI_STATUS_CODE_RE = re.compile(r"<statusCode>\s*(-?\d+)\s*</statusCode>")
_ISAPI_SUB_STATUS_RE = re.compile(r"<subStatusCode>\s*(\w+)\s*</subStatusCode>")


def _is_success(meta_code: int | None) -> bool:
    """Return True if meta.code indicates success."""
    return meta_code in UNLOCK_SUCCESS_CODES


# ── Protocollo comune per le strategie ────────────────────────────────────────


class UnlockStrategy(Protocol):
    """Protocol for unlock strategies."""

    name: str

    async def execute(
        self,
        serial: str,
        channel: int,
        lock_index: int,
    ) -> UnlockResult:
        """Execute the unlock strategy.

        Args:
            serial: Device serial number.
            channel: Channel number (from camera info).
            lock_index: Zero-based lock/door index.

        Returns:
            UnlockResult with success status.
        """
        ...


# ============================================================================
# Strategia Verified (A0) - ISAPI tunnel VERIFICATO empiricamente
# ============================================================================


class StrategyVerified:
    """Strategia cloud_verified: ISAPI tunnel via /v3/userdevices/v1/isapi.

    Endpoint verificato empiricamente su DS-KV7413EY-IME2 + DS-KH7300EY-WTE2
    + DS-KAD7063 (luglio 2026) tramite reverse-engineering dell'app
    HikConnect iOS con mitmproxy (Reqable + SSL decryption).

    Request (osservato):
        POST {base}/v3/userdevices/v1/isapi
        Content-Type: application/x-www-form-urlencoded
        apiData=PUT /ISAPI/AccessControl/RemoteControl/door/{lock_index+1}
        apiKey=100044
        channelNo={channel}
        deviceSerial={serial}
        method=0

    Response osservata (HTTP 200, JSON):
        {
          "data": "<?xml ...?><ResponseStatus><statusCode>1</statusCode>
                   <statusString>OK</statusString>...</ResponseStatus>",
          "meta": {"code": 200, "message": "..."}
        }

    Successo se meta.code == 200 AND ResponseStatus/statusCode in {0, 1}.
    """

    name: str = STRATEGY_CLOUD_VERIFIED

    def __init__(self, client: HikvisionEyClient) -> None:
        """Initialize con un client cloud API."""
        self._client = client

    async def execute(self, serial: str, channel: int, lock_index: int) -> UnlockResult:
        """Execute the verified ISAPI tunnel strategy."""
        url = endpoints.unlock_verified(self._client.base_url)

        # ISAPI usa door index 1-based: lock_index=0 -> door/1, lock_index=1 -> door/2
        door_index = lock_index + 1
        api_data = f"PUT /ISAPI/AccessControl/RemoteControl/door/{door_index}"

        form = {
            "apiData": api_data,
            "apiKey": VERIFIED_APIKEY,
            "channelNo": str(channel) if channel else VERIFIED_CHANNEL_NO,
            "deviceSerial": serial,
            "method": VERIFIED_METHOD,
        }

        _LOGGER.debug(
            "[Verified] POST %s form={apiData=%s, apiKey=%s, channelNo=%s, deviceSerial=%s}",
            url,
            api_data,
            VERIFIED_APIKEY,
            form["channelNo"],
            serial,
        )

        # SICUREZZA CANCELLETTO PEDONALE (v0.3.6):
        # 4 tentativi entro 2.0s totali (0, 0.6, 1.2, 1.8). Oltre 2s la finestra
        # di attesa naturale dell'utente davanti al citofono è finita e chi ha
        # già iniziato ad aprire con la chiave non deve rischiare di trovarsi
        # il cancelletto riaperto alle spalle. Il retry serve solo a coprire
        # NULLpoint transient di 1-2 secondi; per NULLpoint cronici (30-40s
        # osservati su firmware V2.2.56 build 250306) il retry NON serve, va
        # dato feedback di errore all'utente che ripremerà quando serve.
        RETRY_DELAYS = [0, 0.6, 1.2, 1.8]
        last_error: str | None = None
        last_meta: int | None = None
        last_http: int | None = None
        last_isapi_status: int | None = None

        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay > 0:
                await asyncio.sleep(delay)
                _LOGGER.info(
                    "[Verified] Retry %d/%d after transient error (last: meta=%s isapi=%s)",
                    attempt, len(RETRY_DELAYS), last_meta, last_isapi_status,
                )

            try:
                resp = await self._client.raw_request_form("POST", url, form)
            except Exception as exc:
                last_error = str(exc)
                _LOGGER.warning("[Verified] Attempt %d request failed: %s", attempt, exc)
                continue

            meta_code = _extract_meta_code(resp)
            http_status = resp.get("_http_status")
            isapi_status, isapi_substatus = _extract_isapi_status(resp)

            cloud_ok = _is_success(meta_code)
            isapi_ok = isapi_status is not None and isapi_status in ISAPI_SUCCESS_STATUS_CODES
            success = cloud_ok and (isapi_ok or isapi_status is None)

            # Log SEMPRE il payload di risposta (troncato) quando NON è successo.
            # Serve per diagnosticare cosa sta rispondendo il cloud.
            if not success:
                data_snippet = str(resp.get("data", ""))[:300]
                meta_msg = ""
                try:
                    meta_msg = str(resp.get("meta", {}).get("message", ""))[:200]
                except Exception:  # noqa: BLE001
                    pass
                _LOGGER.warning(
                    "[Verified] Attempt %d NOT confirmed: http=%s meta.code=%s meta.msg=%r isapi.statusCode=%s subStatus=%s data=%r",
                    attempt, http_status, meta_code, meta_msg,
                    isapi_status, isapi_substatus, data_snippet,
                )
            else:
                _LOGGER.info(
                    "[Verified] Unlock CONFIRMED for device %s door %d (channel %s, attempt %d)",
                    serial, door_index, form["channelNo"], attempt,
                )
                return UnlockResult(
                    success=True,
                    strategy=self.name,
                    meta_code=meta_code,
                    http_status=http_status,
                )

            # Log dedicato per il caso NULLpoint (crash transient firmware
            # del monitor). Non aumentiamo il backoff per motivi di sicurezza:
            # meglio fallire in fretta che riaprire il cancello dopo che
            # l'utente ha già aperto manualmente con la chiave.
            if isapi_substatus and "NULLpoint" in str(isapi_substatus):
                _LOGGER.info(
                    "[Verified] Firmware returned NULLpoint (transient crash) — quick retry only"
                )

            last_meta = meta_code
            last_http = http_status
            last_isapi_status = isapi_status
            last_error = (
                f"meta.code={meta_code}, isapi.statusCode={isapi_status}, "
                f"subStatus={isapi_substatus}"
            )

        return UnlockResult(
            success=False,
            strategy=self.name,
            meta_code=last_meta,
            http_status=last_http,
            error=last_error or "unknown",
        )


# ============================================================================
# Strategia A1 - legacy remote/unlock (fallback)
# ============================================================================


class StrategyA1:
    """Strategia A1: legacy remote/unlock endpoint.

    Endpoint originale. Funziona su device IP-nativi (KV81xx/KV61xx).
    Su serie EY il cloud risponde 200 ma il comando non arriva al bus 2-fili.
    """

    name: str = STRATEGY_CLOUD_A1

    def __init__(self, client: HikvisionEyClient) -> None:
        """Initialize with a cloud API client."""
        self._client = client

    async def execute(self, serial: str, channel: int, lock_index: int) -> UnlockResult:
        """Execute Strategy A1."""
        url = endpoints.unlock_a1(self._client.base_url, serial, channel, lock_index)
        _LOGGER.debug("[A1] PUT %s", url)
        try:
            resp = await self._client.raw_request("PUT", url)
        except Exception as exc:
            _LOGGER.warning("[A1] Request failed: %s", exc)
            return UnlockResult(success=False, strategy=self.name, error=str(exc))

        meta_code = _extract_meta_code(resp)
        success = _is_success(meta_code)
        _LOGGER.debug("[A1] Response meta.code=%s → success=%s", meta_code, success)
        if success:
            _LOGGER.info("[A1] Unlock confirmed for device %s lock %d", serial, lock_index)
        else:
            _LOGGER.warning("[A1] Unlock not confirmed for device %s — meta.code=%s", serial, meta_code)

        return UnlockResult(
            success=success,
            strategy=self.name,
            meta_code=meta_code,
            error=None if success else f"meta.code={meta_code}",
        )


# ── Strategia A2 — userdevices smart lock ────────────────────────────────────


class StrategyA2:
    """Strategia A2: userdevices/v1/devices/{serial}/lock endpoint.

    Presente nell'APK Hik-Connect per smart lock device recenti.
    """

    name: str = STRATEGY_CLOUD_A2

    def __init__(self, client: HikvisionEyClient) -> None:
        """Initialize with a cloud API client."""
        self._client = client

    async def execute(self, serial: str, channel: int, lock_index: int) -> UnlockResult:
        """Execute Strategy A2."""
        url = endpoints.unlock_a2(self._client.base_url, serial, lock_index)
        _LOGGER.debug("[A2] PUT %s", url)
        try:
            resp = await self._client.raw_request("PUT", url)
        except Exception as exc:
            _LOGGER.warning("[A2] Request failed: %s", exc)
            return UnlockResult(success=False, strategy=self.name, error=str(exc))

        meta_code = _extract_meta_code(resp)
        success = _is_success(meta_code)
        _LOGGER.debug("[A2] Response meta.code=%s → success=%s", meta_code, success)
        if success:
            _LOGGER.info("[A2] Unlock confirmed for device %s lock %d", serial, lock_index)

        return UnlockResult(
            success=success,
            strategy=self.name,
            meta_code=meta_code,
            error=None if success else f"meta.code={meta_code}",
        )


# ── Strategia A3 — relay control ─────────────────────────────────────────────


class StrategyA3:
    """Strategia A3: relay control per device a bus 2-fili (serie EY).

    Ipotesi: usato per device con uscite relè (KV7413EY → KAD7063).
    Tenta prima il formato primario, poi il formato alternativo con channelNo.
    """

    name: str = STRATEGY_CLOUD_A3

    def __init__(self, client: HikvisionEyClient) -> None:
        """Initialize with a cloud API client."""
        self._client = client

    async def execute(self, serial: str, channel: int, lock_index: int) -> UnlockResult:
        """Execute Strategy A3 (prova primary poi alternative)."""
        # Tentativo primario
        url_primary = endpoints.unlock_a3(self._client.base_url, serial, lock_index)
        _LOGGER.debug("[A3] POST %s (primary)", url_primary)
        try:
            resp = await self._client.raw_request("POST", url_primary)
            meta_code = _extract_meta_code(resp)
            if _is_success(meta_code):
                _LOGGER.info("[A3] Unlock confirmed (primary) for device %s relay %d", serial, lock_index)
                return UnlockResult(success=True, strategy=self.name, meta_code=meta_code)
            _LOGGER.debug("[A3] Primary returned meta.code=%s — trying alternate format", meta_code)
        except Exception as exc:
            _LOGGER.debug("[A3] Primary request failed: %s — trying alternate format", exc)
            meta_code = None

        # Tentativo alternativo con srcId+channelNo
        url_alt = endpoints.unlock_a3_alt(self._client.base_url, serial, channel, lock_index)
        _LOGGER.debug("[A3] POST %s (alternate)", url_alt)
        try:
            resp = await self._client.raw_request("POST", url_alt)
        except Exception as exc:
            _LOGGER.warning("[A3] Alternate request failed: %s", exc)
            return UnlockResult(success=False, strategy=self.name, error=str(exc))

        meta_code = _extract_meta_code(resp)
        success = _is_success(meta_code)
        _LOGGER.debug("[A3] Alternate meta.code=%s → success=%s", meta_code, success)
        if success:
            _LOGGER.info("[A3] Unlock confirmed (alternate) for device %s relay %d", serial, lock_index)

        return UnlockResult(
            success=success,
            strategy=self.name,
            meta_code=meta_code,
            error=None if success else f"meta.code={meta_code}",
        )


# ── Strategia A4 — ISAPI over cloud tunnel ────────────────────────────────────


class StrategyA4:
    """Strategia A4: ISAPI-over-cloud tunnel.

    Invia body XML tramite tunnel cloud. Meccanismo usato dall'app mobile
    Hik-Connect per device ISAPI-native (serie EY).
    Tenta il path primario, poi il fallback se riceve 404.
    """

    name: str = STRATEGY_CLOUD_A4

    def __init__(self, client: HikvisionEyClient) -> None:
        """Initialize with a cloud API client."""
        self._client = client

    async def execute(self, serial: str, channel: int, lock_index: int) -> UnlockResult:
        """Execute Strategy A4 (primary path + 404 fallback)."""
        url_primary = endpoints.unlock_a4_primary(self._client.base_url, serial)
        _LOGGER.debug("[A4] POST %s (ISAPI tunnel primary)", url_primary)

        try:
            resp = await self._client.raw_request_xml("POST", url_primary, _ISAPI_DOOR_OPEN_XML)
        except Exception as exc:
            _LOGGER.warning("[A4] Primary XML request failed: %s", exc)
            return UnlockResult(success=False, strategy=self.name, error=str(exc))

        meta_code = _extract_meta_code(resp)
        http_status = resp.get("_http_status")

        if http_status == 404 or meta_code == 404:
            # Prova il path alternativo
            url_fallback = endpoints.unlock_a4_fallback(self._client.base_url, serial)
            _LOGGER.debug("[A4] 404 on primary — trying fallback %s", url_fallback)
            try:
                resp = await self._client.raw_request_xml("POST", url_fallback, _ISAPI_DOOR_OPEN_XML)
                meta_code = _extract_meta_code(resp)
                http_status = resp.get("_http_status")
            except Exception as exc:
                _LOGGER.warning("[A4] Fallback XML request failed: %s", exc)
                return UnlockResult(success=False, strategy=self.name, error=str(exc))

        success = _is_success(meta_code) or (http_status == 200 and meta_code is None)
        _LOGGER.debug("[A4] meta.code=%s, http_status=%s → success=%s", meta_code, http_status, success)
        if success:
            _LOGGER.info("[A4] Unlock confirmed via ISAPI tunnel for device %s", serial)

        return UnlockResult(
            success=success,
            strategy=self.name,
            meta_code=meta_code,
            http_status=http_status,
            error=None if success else f"meta.code={meta_code}, http={http_status}",
        )


# ── Strategia Local — ISAPI locale ────────────────────────────────────────────


# StrategyLocal rimossa in v0.3.6 (porte ISAPI chiuse sul serie EY).
# Vedi docstring del modulo per il ragionamento.


# ── Auto-selection ────────────────────────────────────────────────────────────


class UnlockManager:
    """Gestisce la selezione e l'esecuzione delle strategie di unlock.

    Ordine di default: Local (se disponibile) → A4 → A3 → A2 → A1
    Memorizza la strategia vincente in preferred_strategy per uso futuro.
    """

    def __init__(
        self,
        client: HikvisionEyClient,
        isapi_client: LocalISAPIClient | None = None,
        preferred_strategy: str | None = None,
    ) -> None:
        """Initialize the unlock manager.

        Args:
            client: Cloud API client.
            isapi_client: Optional local ISAPI client.
            preferred_strategy: Previously successful strategy to try first.
        """
        self._client = client
        self._isapi_client = isapi_client
        self.preferred_strategy = preferred_strategy

        # Registro delle strategie disponibili
        self._strategy_map: dict[str, UnlockStrategy] = {
            STRATEGY_CLOUD_VERIFIED: StrategyVerified(client),
            STRATEGY_CLOUD_A1: StrategyA1(client),
            STRATEGY_CLOUD_A2: StrategyA2(client),
            STRATEGY_CLOUD_A3: StrategyA3(client),
            STRATEGY_CLOUD_A4: StrategyA4(client),
        }
        # v0.3.6: nessuna registrazione di StrategyLocal (porte chiuse).
        # isapi_client resta memorizzato solo per compatibilità futura.

    # v0.3.6: solo cloud_verified è considerata valida come strategia preferita
    # salvata. Le A1-A4 sono legacy sperimentali e non devono mai essere
    # promosse in cima all'ordine (v0.3.2). STRATEGY_LOCAL rimosso.
    _VALID_PREFERRED = {STRATEGY_CLOUD_VERIFIED}

    def _build_auto_order(self) -> list[str]:
        """Build the strategy execution order for 'auto' mode."""
        order: list[str] = []

        # 1. Strategia preferita per prima — SOLO se è verified o local.
        #    Se in options c'è una A1-A4 salvata da versioni precedenti,
        #    la ignoriamo silenziosamente (verrà sovrascritta al primo
        #    successo di verified).
        if (
            self.preferred_strategy
            and self.preferred_strategy != STRATEGY_AUTO
            and self.preferred_strategy in self._VALID_PREFERRED
            and self.preferred_strategy in self._strategy_map
        ):
            order.append(self.preferred_strategy)
        elif self.preferred_strategy and self.preferred_strategy not in self._VALID_PREFERRED:
            _LOGGER.info(
                "[UnlockManager] Ignoring stale preferred_strategy=%s (legacy A1-A4). "
                "Will use cloud_verified first.",
                self.preferred_strategy,
            )

        # 2. Verified per prima (strategia empiricamente confermata sul serie EY)
        if STRATEGY_CLOUD_VERIFIED in self._strategy_map and STRATEGY_CLOUD_VERIFIED not in order:
            order.append(STRATEGY_CLOUD_VERIFIED)
        # 3. Cloud legacy A4 -> A3 -> A2 -> A1 (fallback per hardware diverso)
        for s in [STRATEGY_CLOUD_A4, STRATEGY_CLOUD_A3, STRATEGY_CLOUD_A2, STRATEGY_CLOUD_A1]:
            if s not in order:
                order.append(s)
        return order

    async def open_gate(
        self,
        serial: str,
        channel: int = 1,
        lock_index: int = 0,
        strategy: str = STRATEGY_AUTO,
    ) -> UnlockResult:
        """Attempt to open a gate/door.

        Args:
            serial: Device serial number.
            channel: Channel number (from camera info).
            lock_index: Zero-based lock index.
            strategy: Which strategy to use (default 'auto').

        Returns:
            UnlockResult of the first successful strategy.

        Raises:
            UnlockFailed: If all applicable strategies fail.
        """
        if strategy != STRATEGY_AUTO:
            # Strategia esplicita: esegui solo quella
            strat = self._strategy_map.get(strategy)
            if strat is None:
                raise UnlockFailed(strategy, None)
            _LOGGER.info("[UnlockManager] Using explicit strategy: %s", strategy)
            result = await strat.execute(serial, channel, lock_index)
            if result.success:
                _LOGGER.info("[UnlockManager] Strategy %s succeeded", strategy)
            else:
                _LOGGER.warning("[UnlockManager] Strategy %s failed: %s", strategy, result.error)
            return result

        # Modalità auto: prova in ordine
        order = self._build_auto_order()
        _LOGGER.info("[UnlockManager] Auto mode — trying order: %s", order)

        for strat_name in order:
            strat = self._strategy_map.get(strat_name)
            if strat is None:
                continue
            _LOGGER.info("[UnlockManager] Trying strategy: %s", strat_name)
            result = await strat.execute(serial, channel, lock_index)
            if result.success:
                _LOGGER.info("[UnlockManager] Strategy %s succeeded — will use as preferred next time", strat_name)
                self.preferred_strategy = strat_name
                return result
            _LOGGER.warning("[UnlockManager] Strategy %s failed: %s", strat_name, result.error)

        # Tutte le strategie fallite
        tried = ", ".join(order)
        raise UnlockFailed(f"all ({tried})", None)

    def get_strategy_names(self) -> list[str]:
        """Return list of available strategy names."""
        return list(self._strategy_map.keys())


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_meta_code(resp: dict[str, Any]) -> int | None:
    """Extract meta.code from a cloud API response dict."""
    try:
        return int(resp["meta"]["code"])
    except (KeyError, TypeError, ValueError):
        return None


def _extract_isapi_status(resp: dict[str, Any]) -> tuple[int | None, str | None]:
    """Extract ISAPI statusCode+subStatusCode dal campo `data` della response.

    Il tunnel ISAPI di Hik-Connect restituisce il payload XML del device
    dentro il campo `data` (string) della response JSON:

        {"data": "<?xml ...?><ResponseStatus>...
                    <statusCode>1</statusCode>
                    <statusString>OK</statusString>
                    <subStatusCode>ok</subStatusCode>...",
         "meta": {"code": 200}}

    Returns:
        Tupla (statusCode, subStatusCode). Entrambi None se non parsabili.
    """
    data = resp.get("data")
    if not isinstance(data, str):
        return None, None

    status_code: int | None = None
    substatus: str | None = None

    match = _ISAPI_STATUS_CODE_RE.search(data)
    if match:
        try:
            status_code = int(match.group(1))
        except ValueError:
            status_code = None

    sub_match = _ISAPI_SUB_STATUS_RE.search(data)
    if sub_match:
        substatus = sub_match.group(1)

    return status_code, substatus
