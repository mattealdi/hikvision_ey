"""DataUpdateCoordinator per l'integrazione Hikvision EY.

Due coordinator separati:
1. DeviceCoordinator — aggiornamento device list ogni 300s
2. CallStatusCoordinator — aggiornamento call status adattivo (3s ringing, 30s idle)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HikvisionEyClient, LocalISAPIClient, UnlockManager
from .api.exceptions import AuthError, CaptchaRequired, DeviceOffline, HikvisionEyError, UnlockFailed
from .api.models import CallStatus, DeviceInfo, UnlockResult
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

# v0.5.0: TTL (secondi) di un override di reachability derivato dal probe
# live get_call_status. Copre più cicli di polling del cloud stale.
_REACHABILITY_TTL_S = 120
_REACHABILITY_TTL_ERROR_S = 45
# Versione dello storage persistente dello stato diagnostico.
_STATE_STORE_VERSION = 1


@dataclass
class _ReachabilityOverride:
    """Override temporaneo dello stato online, con scadenza monotonic."""

    online: bool | None
    source: str
    expires_at: float


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

        # ---- v0.5.0: overlay reachability + persistenza stato --------------
        # Il cloud Hik-Connect mantiene globalStatus=1 stale dopo un reboot
        # locale del monitor. Il probe live get_call_status (CallStatusCoordinator)
        # rileva DeviceOffline in modo più affidabile e marca qui un override
        # temporaneo che prevale sul dato cloud finché non scade.
        self._reachability_overrides: dict[str, _ReachabilityOverride] = {}
        # Store persistente per stato diagnostico e contatori (sopravvive ai
        # restart di HA). Fonte di verità; i RestoreSensor sono solo display.
        self._store: Store = Store(
            hass, _STATE_STORE_VERSION, f"{DOMAIN}_{entry.entry_id}_state"
        )

        # ---- v0.4.0: safety layer per apertura cancelletto ------------------
        # Serializza le richieste di apertura (una alla volta) + cooldown per
        # evitare doppie pressioni + task cancellabile via bottone "Annulla".
        # Il cancelletto ha chiusura MANUALE (nessuna richiusura automatica),
        # quindi il bottone Annulla è la protezione principale.
        self._unlock_lock = asyncio.Lock()
        self._last_unlock_end_ts: float = 0.0     # monotonic, fine ultima apertura
        self._last_unlock_success: bool = False   # esito ultima apertura
        self._current_unlock_task: asyncio.Task | None = None  # task in flight
        self._is_unlocking: bool = False          # True durante l'attesa

        # Stats esposti come sensori diagnostici (aggiornati a ogni pressione).
        self.last_unlock_stats: dict[str, Any] = {
            "esito": None,       # 'ok' | 'bug_nullpoint' | 'timeout' | 'errore' | 'ignorato_cooldown' | 'annullato'
            "tentativi": None,   # int (attualmente non popolato da unlock_manager)
            "durata_ms": None,   # int
            "strategia": None,   # str | None
            "timestamp": None,   # iso str
        }

        # Storico ultime 10 aperture (diagnostica esteso). Ogni entry:
        # {"esito": str, "durata_ms": int, "strategia": str|None, "timestamp": iso}
        self.unlock_history: deque[dict[str, Any]] = deque(maxlen=10)

        # Contatori chiamate (per sensori Chiamate Oggi/Totali).
        # Aggiornati dai listener eventi doorbell/call in __init__.py.
        self.call_count_today: int = 0
        self.call_count_total: int = 0
        self._call_count_day: str | None = None  # 'YYYY-MM-DD' per rollover

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
            # v0.5.0: applica gli override di reachability ancora validi, così
            # un DeviceOffline rilevato dal probe live prevale sul cloud stale.
            self._apply_reachability_overrides(devices)
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

    # ------------------------------------------------------------------
    # v0.5.0 — Reachability overlay (Bug A)
    # ------------------------------------------------------------------
    def _apply_reachability_overrides(self, devices: list[DeviceInfo]) -> None:
        """Sovrascrive is_online sui device con override live ancora validi."""
        now = time.monotonic()
        # Pulisci override scaduti
        self._reachability_overrides = {
            s: ov for s, ov in self._reachability_overrides.items()
            if ov.expires_at > now
        }
        for dev in devices:
            ov = self._reachability_overrides.get(dev.serial)
            if ov is None:
                continue
            dev.is_online = ov.online
            dev.online_source = ov.source
            if ov.online is False:
                dev.local_ip = dev.wan_ip = dev.wifi_signal = None

    @callback
    def mark_device_reachability(
        self, serial: str, online: bool | None, source: str, ttl: int = _REACHABILITY_TTL_S
    ) -> None:
        """Registra un override live dello stato online (dal probe get_call_status).

        Prevale sul dato cloud (che può restare stale dopo un reboot locale)
        finché non scade. Aggiorna subito i device già in memoria e notifica.
        """
        self._reachability_overrides[serial] = _ReachabilityOverride(
            online=online,
            source=source,
            expires_at=time.monotonic() + ttl,
        )
        changed = False
        for dev in self.data or []:
            if dev.serial == serial and (dev.is_online is not online or dev.online_source != source):
                dev.is_online = online
                dev.online_source = source
                if online is False:
                    dev.local_ip = dev.wan_ip = dev.wifi_signal = None
                changed = True
        if changed:
            self.async_update_listeners()

    # ------------------------------------------------------------------
    # v0.5.0 — Persistenza stato diagnostico (Bug B)
    # ------------------------------------------------------------------
    async def async_load_persistent_state(self) -> None:
        """Reidrata stato diagnostico e contatori dallo Store all'avvio."""
        data = await self._store.async_load() or {}
        stored_stats = data.get("last_unlock_stats")
        if isinstance(stored_stats, dict):
            self.last_unlock_stats.update(stored_stats)
        for entry in data.get("unlock_history") or []:
            self.unlock_history.append(entry)
        self.call_count_today = int(data.get("call_count_today") or 0)
        self.call_count_total = int(data.get("call_count_total") or 0)
        self._call_count_day = data.get("call_count_day")
        _LOGGER.debug(
            "[DeviceCoordinator] Stato persistente ricaricato: esito=%s calls_total=%d",
            self.last_unlock_stats.get("esito"), self.call_count_total,
        )

    @callback
    def _schedule_save_state(self) -> None:
        """Salva (debounced) lo stato diagnostico corrente su disco."""
        self._store.async_delay_save(self._state_to_persist, 2)

    @callback
    def _state_to_persist(self) -> dict[str, Any]:
        """Snapshot serializzabile dello stato diagnostico."""
        return {
            "last_unlock_stats": self.last_unlock_stats,
            "unlock_history": list(self.unlock_history),
            "call_count_today": self.call_count_today,
            "call_count_total": self.call_count_total,
            "call_count_day": self._call_count_day,
        }

    def update_preferred_strategy(self, strategy: str) -> None:
        """Persist the preferred unlock strategy in entry options.

        Args:
            strategy: Strategy name that succeeded last.
        """
        self.unlock_manager.preferred_strategy = strategy
        new_options = {**self._entry.options, CONF_PREFERRED_STRATEGY: strategy}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        _LOGGER.info("[DeviceCoordinator] Preferred strategy saved: %s", strategy)

    # ------------------------------------------------------------------
    # v0.4.2 — Apertura sicura del cancelletto (fix regressione v0.4.1)
    # ------------------------------------------------------------------
    #
    # PERCHÉ v0.4.2: in v0.4.1 il cancelletto NON apriva. La causa NON era
    # il firmware né il wrapper in sé, ma il RETRY LOOP dentro
    # StrategyVerified (`for attempt in range(1, 46)`, delay 2s/3s): teneva
    # occupato il coordinator fino a ~90s ribattendo sulla stessa richiesta
    # condannata al NULLpoint, così UnlockManager non arrivava MAI a provare
    # la cascata di fallback A4→A3→A2→A1 — che è ciò che in v0.3.2 apre il
    # relè. v0.4.2 rimuove quel loop: StrategyVerified torna single-shot,
    # fallisce in ~500ms e cede subito la mano alla cascata rapida.
    #
    # Strategia v0.4.2 (wrapper snellito):
    #  - Cap safety 15s (era 90s): la cascata single-shot dura ~3-5s.
    #  - Nessun retry adattivo (rimosso): il tempo si spende provando
    #    chiavi diverse, non ribattendo su quella sbagliata.
    #  - Cooldown post-fail 20s (era 60s), post-ok 3s (invariato).
    #  - Auto-cancel su EVENT_CALL_ENDED + bottone Annulla: invariati.
    # ------------------------------------------------------------------
    #
    # Contesto:
    # - Firmware V2.2.56 build 250306 affetto da bug NULLpoint sull'endpoint
    #   PUT /ISAPI/AccessControl/RemoteControl/door/1: HTTP 200 dal cloud ma
    #   subStatusCode=NULLpoint errorCode=805306388. Fix atteso su V2.2.66
    #   (mail già inviata a support.it@hikvision.com). Nel frattempo la via
    #   che apre è la cascata di chiavi legacy A4→A3→A2→A1.
    # - Porte TCP monitor (80/443/554/8000/8080/8200) tutte CHIUSE
    #   (verificato 3/7/2026): STRATEGY_LOCAL non attivabile, già rimossa.
    # - Cancelletto pedonale a chiusura MANUALE (nessuna richiusura
    #   automatica): con cap 15s e single-shot non c'è finestra di
    #   riapertura tardiva dopo che l'utente ha aperto con la chiave.
    #
    # Regole implementate:
    #  1) Cap safety 15s: dopo 15s la richiesta viene abbandonata.
    #  2) Nessun retry interno alle strategie: single-shot + cascata.
    #  3) Cooldown 20s dopo un fallimento: evita doppio-click mentre l'utente
    #     si sta già muovendo verso la chiave.
    #  4) Cooldown 3s dopo un successo: previene doppio click accidentale.
    #  5) Serializzazione via asyncio.Lock: mai due open_gate in flight.
    #  6) Task cancellabile: il coordinator memorizza il task in _current_
    #     unlock_task; il bottone "Annulla Apertura" chiama cancel_unlock()
    #     che invoca task.cancel() istantaneamente.
    #  7) Binary sensor "Apertura in Corso" (attributo self._is_unlocking)
    #     riflette lo stato per la UI e le automazioni.
    #
    # Il timestamp guard v0.3.6 (rigetto risposte >5s) resta RIMOSSO: con
    # cascata single-shot e cap 15s la protezione è già nella finestra breve,
    # più il bottone Annulla + auto-cancel.

    # v0.4.2: wrapper SNELLITO. Con StrategyVerified tornata single-shot,
    # la cascata completa cloud_verified→A4→A3→A2→A1 dura ~3-5s: non serve
    # più il cap 90s della v0.4.1 (che copriva il retry loop, ora rimosso).
    #  - Cap safety 15s (era 90s): margine ampio per la cascata single-shot,
    #    ma taglia netto qualsiasi apertura tardiva su cancelletto a chiusura
    #    manuale.
    #  - Cooldown post-fail 20s (era 60s): evita il doppio-click accidentale
    #    mentre l'utente si sta già muovendo verso la chiave, senza tenere il
    #    bottone "morto" per un minuto intero.
    #  - Cooldown post-ok 3s (invariato): previene la doppia pressione.
    _UNLOCK_TIMEOUT_S: float = 15.0
    _UNLOCK_COOLDOWN_OK_S: float = 3.0
    # v0.5.0: ridotto 20 -> 5s. Con il fix single-shot (v0.4.2) un fallimento
    # non innesca più raffiche di retry, quindi non serve un cooldown lungo:
    # 5s bastano a evitare doppie pressioni accidentali senza costringere
    # l'utente ad aspettare 20s per ritentare un'apertura legittima.
    _UNLOCK_COOLDOWN_FAIL_S: float = 5.0

    @property
    def is_unlocking(self) -> bool:
        """True mentre una richiesta di apertura è in corso."""
        return self._is_unlocking

    async def cancel_unlock(self) -> bool:
        """Annulla l'apertura in corso, se esiste.

        Chiamata dal bottone "Annulla Apertura" e dal servizio
        `hikvision_ey.cancel_unlock`. Cancella il task asyncio in flight
        (i tentativi in corso vengono interrotti) e forza esito="annullato".

        Returns:
            True se un task era in corso ed è stato annullato, False altrimenti.
        """
        task = self._current_unlock_task
        if task is None or task.done():
            _LOGGER.info("[Unlock] cancel_unlock: nessuna apertura in corso")
            return False

        _LOGGER.warning("[Unlock] ANNULLAMENTO richiesto dall'utente — cancello task in flight")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        return True

    async def open_gate_safely(
        self,
        serial: str,
        channel: int = 1,
        lock_index: int = 0,
        strategy: str = "auto",
    ) -> UnlockResult:
        """Open gate with safety guards.

        Applica:
        - Cooldown 30s dopo fallimento / 3s dopo successo
        - Timeout hard 15s
        - Task cancellabile via cancel_unlock()
        - Aggiornamento stats + storico + stato is_unlocking

        Ritorna SEMPRE un UnlockResult e non solleva mai UnlockFailed: tutti
        gli errori terminali (incluso il bug NULLpoint del firmware) sono
        catturati e trasformati in result.success=False, così il chiamante
        (button/service) ha un contratto uniforme e decide lui se segnalare.
        """
        import time
        loop_time = time.monotonic

        # v0.5.0: cooldown + esecuzione TUTTI dentro lo stesso lock.
        # Prima il check cooldown era fuori dal lock: due pressioni concorrenti
        # potevano superarlo entrambe (leggevano _last_unlock_end_ts prima che
        # l'altra lo aggiornasse) e serializzarsi comunque, aggirando il
        # cooldown. Ora il controllo avviene dopo l'acquisizione del lock,
        # quindi è atomico rispetto all'aggiornamento del timestamp.
        async with self._unlock_lock:
            press_ts = loop_time()

            # 1) Cooldown differenziato: 5s dopo fail, 3s dopo ok
            cooldown = (
                self._UNLOCK_COOLDOWN_OK_S if self._last_unlock_success
                else self._UNLOCK_COOLDOWN_FAIL_S
            )
            elapsed_since_end = press_ts - self._last_unlock_end_ts
            if self._last_unlock_end_ts > 0 and elapsed_since_end < cooldown:
                residual = cooldown - elapsed_since_end
                _LOGGER.warning(
                    "[Unlock] Cooldown attivo (%.1fs residui, %s) — richiesta ignorata",
                    residual, "post-ok" if self._last_unlock_success else "post-fail",
                )
                self._update_stats(
                    esito="ignorato_cooldown",
                    tentativi=0,
                    durata_ms=0,
                    strategia=None,
                )
                return UnlockResult(
                    success=False,
                    strategy="cooldown",
                    error=f"cooldown {residual:.0f}s residui",
                )

            # 2) Esecuzione con timeout hard
            start_ts = loop_time()
            self._is_unlocking = True
            self.async_update_listeners()  # notifica binary sensor "Apertura in Corso"

            # Creiamo il task come attributo istanza per poterlo cancellare
            # dal bottone Annulla. asyncio.wait_for + task riferito.
            unlock_coro = self.unlock_manager.open_gate(
                serial=serial,
                channel=channel,
                lock_index=lock_index,
                strategy=strategy,
            )
            self._current_unlock_task = asyncio.create_task(unlock_coro)

            try:
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(self._current_unlock_task),
                        timeout=self._UNLOCK_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    # Timeout hard raggiunto: cancella il task
                    self._current_unlock_task.cancel()
                    try:
                        await self._current_unlock_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    elapsed_ms = int((loop_time() - start_ts) * 1000)
                    _LOGGER.warning(
                        "[Unlock] Timeout hard %.1fs — apertura non riuscita",
                        self._UNLOCK_TIMEOUT_S,
                    )
                    self._register_end(
                        success=False,
                        esito="timeout",
                        durata_ms=elapsed_ms,
                        strategia=None,
                    )
                    return UnlockResult(
                        success=False,
                        strategy="timeout",
                        error=f"timeout {self._UNLOCK_TIMEOUT_S:.0f}s",
                    )
                except asyncio.CancelledError:
                    # Annullato via cancel_unlock()
                    elapsed_ms = int((loop_time() - start_ts) * 1000)
                    _LOGGER.info("[Unlock] Task cancellato via Annulla utente")
                    self._register_end(
                        success=False,
                        esito="annullato",
                        durata_ms=elapsed_ms,
                        strategia=None,
                    )
                    return UnlockResult(
                        success=False,
                        strategy="annullato",
                        error="annullato dall'utente",
                    )
                except UnlockFailed as exc:
                    # v0.5.0: coerenza col contratto "ritorna sempre UnlockResult".
                    # Prima qui si rilanciava (raise), contraddicendo il docstring
                    # e costringendo il chiamante a un try/except separato dal
                    # normale flusso success=False. Il bug NULLpoint del firmware
                    # è un errore PERMANENTE (non transitorio): nessun retry ha
                    # senso, quindi lo classifichiamo e lo restituiamo come esito
                    # gestito, uguale a qualsiasi altro fallimento terminale.
                    elapsed_ms = int((loop_time() - start_ts) * 1000)
                    is_nullpoint = "NULLpoint" in str(exc) or "805306388" in str(exc)
                    esito = "bug_nullpoint" if is_nullpoint else "errore"
                    self._register_end(
                        success=False,
                        esito=esito,
                        durata_ms=elapsed_ms,
                        strategia=None,
                    )
                    return UnlockResult(
                        success=False,
                        strategy=esito,
                        error=str(exc),
                    )

                # Esecuzione completata (successo o fail "gestito")
                elapsed_ms = int((loop_time() - start_ts) * 1000)
                if result.success:
                    _LOGGER.info(
                        "[Unlock] OK strategia=%s durata=%dms",
                        result.strategy, elapsed_ms,
                    )
                    self._register_end(
                        success=True,
                        esito="ok",
                        durata_ms=elapsed_ms,
                        strategia=result.strategy,
                    )
                else:
                    is_nullpoint = result.error and (
                        "NULLpoint" in str(result.error)
                        or "805306388" in str(result.error)
                    )
                    self._register_end(
                        success=False,
                        esito="bug_nullpoint" if is_nullpoint else "errore",
                        durata_ms=elapsed_ms,
                        strategia=result.strategy,
                    )
                return result
            finally:
                self._current_unlock_task = None
                self._is_unlocking = False
                self.async_update_listeners()

    def _register_end(
        self,
        *,
        success: bool,
        esito: str,
        durata_ms: int,
        strategia: str | None,
    ) -> None:
        """Registra fine apertura: aggiorna stats, storico, timestamp, esito."""
        import time
        self._last_unlock_end_ts = time.monotonic()
        self._last_unlock_success = success
        self._update_stats(
            esito=esito,
            tentativi=None,
            durata_ms=durata_ms,
            strategia=strategia,
        )
        # Storico ultime 10
        self.unlock_history.append({
            "esito": esito,
            "durata_ms": durata_ms,
            "strategia": strategia,
            "timestamp": _now_iso(),
        })

    def reset_stats(self) -> None:
        """Azzera contatori e storico diagnostici (servizio reset_stats)."""
        _LOGGER.info("[DeviceCoordinator] Reset statistiche diagnostiche")
        self.call_count_today = 0
        self.call_count_total = 0
        self._call_count_day = None
        self.unlock_history.clear()
        # v0.5.0: timestamp valorizzato + flag reset per evitare che i
        # RestoreSensor "resuscitino" il valore precedente dopo un reset.
        self.last_unlock_stats = {
            "esito": None,
            "tentativi": None,
            "durata_ms": None,
            "strategia": None,
            "timestamp": _now_iso(),
            "reset": True,
        }
        self._schedule_save_state()
        self.async_update_listeners()

    def increment_call_count(self) -> None:
        """Incrementa i contatori chiamate (oggi + totale) con rollover giorno."""
        from datetime import date
        today = date.today().isoformat()
        if self._call_count_day != today:
            self.call_count_today = 0
            self._call_count_day = today
        self.call_count_today += 1
        self.call_count_total += 1
        self._schedule_save_state()
        self.async_update_listeners()

    def _update_stats(
        self,
        *,
        esito: str | None,
        tentativi: int | None,
        durata_ms: int | None,
        strategia: str | None,
    ) -> None:
        """Aggiorna last_unlock_stats, persiste e notifica i listener."""
        self.last_unlock_stats = {
            "esito": esito,
            "tentativi": tentativi,
            "durata_ms": durata_ms,
            "strategia": strategia,
            "timestamp": _now_iso(),
        }
        # v0.5.0: persistenza su disco (debounced) + notifica i sensori
        # diagnostici che l'attributo è cambiato senza forzare un refresh.
        self._schedule_save_state()
        self.async_update_listeners()


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
        """Get serials of devices to poll for call status.

        v0.5.0: NON filtriamo più su is_online. Il probe serve proprio a
        rilevare offline reale (nonostante il cloud stale) e il recovery:
        se saltassimo i device offline, non ci accorgeremmo mai del ritorno
        online.
        """
        if not self._device_coordinator.data:
            return []
        return [d.serial for d in self._device_coordinator.data]

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
        """Fetch call status for a single device.

        v0.5.0: usa l'esito come heartbeat live per aggiornare la reachability
        del device coordinator (prevale sul globalStatus cloud stale).
        """
        try:
            status = await self._client.get_call_status(serial)
        except DeviceOffline:
            self._device_coordinator.mark_device_reachability(
                serial, False, "call_status", ttl=_REACHABILITY_TTL_S
            )
            return CallStatus(status="offline")
        except (HikvisionEyError, aiohttp.ClientError) as exc:
            # Errore transitorio: stato incerto → None (né online né offline).
            self._device_coordinator.mark_device_reachability(
                serial, None, "call_status_error", ttl=_REACHABILITY_TTL_ERROR_S
            )
            raise UpdateFailed(f"Call status error for {serial}") from exc
        else:
            # Risposta valida = device raggiungibile ora.
            self._device_coordinator.mark_device_reachability(
                serial, True, "call_status", ttl=_REACHABILITY_TTL_S
            )
            return status

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
