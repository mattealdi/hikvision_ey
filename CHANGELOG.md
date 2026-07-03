# Changelog

Tutte le modifiche rilevanti al progetto sono documentate qui.
Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).

## [0.3.3] - 2026-07-03

### Fixed
- **Fix stale preferred_strategy**: in versioni precedenti la strategia
  `cloud_a1` poteva essere erroneamente salvata come preferita in
  `entry.options`, portando `UnlockManager` a provarla come primo tentativo
  invece di `cloud_verified` (l'unica confermata funzionante su serie EY).
  Alla partenza il coordinator ora ripulisce automaticamente qualsiasi
  `preferred_strategy` non valida (accettate solo `cloud_verified` e `local`).
- **Retry automatico su cloud_verified**: la strategia principale ora tenta
  una seconda volta dopo 800ms in caso di errore transient del cloud
  Hik-Connect (osservato empiricamente il 3 luglio 2026 mattina: 4 pressioni
  consecutive fallite tutte con stesso errore cloud). Nessun impatto sul
  tempo di risposta in caso di successo al primo colpo.

### Changed
- **Logging molto più esplicito sui fallimenti unlock**: quando la strategia
  `cloud_verified` fallisce, il log WARNING ora mostra `http_status`,
  `meta.code`, `meta.message`, `isapi.statusCode`, `subStatusCode` e i
  primi 300 caratteri del campo `data` restituito dal cloud. Serve a
  diagnosticare in fretta le cause reali dei fallimenti (limiti API,
  device offline, permessi, ecc.).
- `UnlockManager._build_auto_order()` non promuove più in cima strategie
  legacy A1-A4 anche se presenti in `preferred_strategy`: solo
  `cloud_verified` e `local` sono considerate preferibili di default.

## [0.3.2] - 2026-07-02

### Fixed
- **Logo integrazione ora visibile in HA**: le icone erano in
  `custom_components/hikvision_ey/icon.png` ma HA 2026.3+ le carica dalla
  sottocartella `brand/`. Spostate in `custom_components/hikvision_ey/brand/`
  con tutte e 4 le varianti richieste dallo standard HA:
  - `icon.png` (256×256)
  - `icon@2x.png` (512×512)
  - `logo.png` (256×256)
  - `logo@2x.png` (512×512)
- **Rimosso il sensore duplicato "Segnale WiFi" orfano** dalla v0.3.0.
  Il cleanup automatico ora rimuove anche `_rssi`.

### Changed
- Il sensore percentuale WiFi ora si chiama **"Segnale WiFi"** (invece
  di "Qualità WiFi") — il valore rimane in percentuale 0-100%,
  cambia solo l'etichetta per essere più naturale in italiano.

### Removed
- **Binary sensor `monitor_online` e `outdoor_online`**: entrambi
  restituivano identicamente `dev.is_online`, duplicando il sensore
  `online`. Erano i 2 dei 4 sensori "Citofono Casa — Non disponibile"
  che appesantivano la lista.
- Cleanup automatico esteso: rimuove dal registry anche le entità
  `_rssi`, `_monitor_online`, `_outdoor_online` orfane.

### Result
Dopo l'update l'integrazione mostra un set ancora più snello:
- **7 button** (invariati)
- **4 binary_sensor**: Online, Cloud Connected, Doorbell Ringing, Call Active
- **9 sensor** (invariati)

Totale: **20 entità** invece di 22 (rimossi 2 duplicati).

Dei restanti 2 sensori "Citofono Casa — Non disponibile" che vedi in
foto, questi sono `Doorbell Ringing` (campanello che suona) e
`Call Active` (chiamata in corso): **si popolano SOLO quando qualcuno
suona al citofono**. Sono lo stato istantaneo, non un errore.

## [0.3.1] - 2026-07-02

### Removed
- **Piattaforma `camera` completamente rimossa**. Il modello DS-KH7300EY-WTE2
  non espone RTSP e il cloud Hik-Connect restituiva 50 canali placeholder
  vuoti (`camera 1@…` … `camera 50@…`) che ingolfavano la UI di HA.
- Rimosso il file `camera.py` e i metodi ausiliari `rtsp_stream_url()` /
  `get_snapshot()` da `LocalISAPIClient` (non più utilizzati).
- Rimosso il sensore `uptime_info` (duplicava il firmware).

### Fixed
- Sensore WiFi: il cloud Hik-Connect espone `signal` come **percentuale
  0-100%**, non dBm. Corretto: nuovo sensore `wifi_quality` con unit
  `%` e device_class None. Prima si vedeva l'assurdo `100 dBm`.
- Corretti tutti i nomi entità in `strings.json`, `translations/en.json`,
  `translations/it.json` (rimossa sezione `camera`, rinominato `rssi`
  → `wifi_quality`).

### Added
- **Cleanup automatico dell'entity registry al primo avvio dopo
  l'aggiornamento**: `_async_cleanup_stale_entities()` rimuove
  automaticamente le entità `camera.*` e `sensor.*_uptime_info`
  lasciate da versioni precedenti, così l'utente non deve pulirle
  a mano.

### Result
Dopo l'update l'integrazione mostra un set snello e coerente:
- **7 button**: Aggiorna Token, Apri Cancelletto, Apri Porta 1, Apri
  Porta 2, Riaggancia, Riavvia Dispositivo, Rispondi Chiamata
- **6 binary_sensor**: Online, Cloud Connected, Doorbell Ringing,
  Call Active, Monitor Online, Outdoor Station Online
- **9 sensor**: Firmware, Nome Dispositivo, Numero Seriale, Stato Cloud,
  Ultima Chiamata, Ultimo Evento, Contatore Chiamate, **Qualità WiFi %**,
  Indirizzo WiFi

Totale: **22 entità** invece delle **~66 di v0.3.0** (50 camere fake
+ 2 duplicati + 14 utili).

## [0.3.0] - 2026-07-02

### Added
- **Camera entity funzionante**: la piattaforma `camera` ora restituisce
  lo stream RTSP live dal monitor DS-KH7300EY e uno snapshot JPEG
  on-demand via ISAPI (`/Streaming/channels/101/picture`).
- Metodi `LocalISAPIClient.rtsp_stream_url(channel, stream)` e
  `LocalISAPIClient.get_snapshot(channel)` in `api/isapi.py`.
- Proprietà `host`, `username`, `password` esposte da `LocalISAPIClient`
  per costruzione URL RTSP.
- **Icona integrazione**: `custom_components/hikvision_ey/icon.png`
  (256×256) mostrata in Home Assistant nella dashboard integrazioni.

### Changed
- La camera entity è ora abilitata **solo** quando è configurato il
  client ISAPI locale (serve IP + credenziali admin del monitor).
  In modalità solo-cloud la piattaforma non registra entity camera.
- `stream_source()` usa lo sub-stream (canale 102) per minore latenza.
- Corretto codeowner e URL documentazione in `manifest.json`:
  `@mattealdi` (repo pubblico `github.com/mattealdi/hikvision_ey`).
- Bump `manifest.json` e `pyproject.toml` → 0.3.0.

### Notes
- Logo/icona brand ufficiale in via di pubblicazione su
  [home-assistant/brands](https://github.com/home-assistant/brands)
  (PR in corso, path `custom_integrations/hikvision_ey/`).

## [0.2.0] - 2026-07-02

### 🎯 Endpoint di unlock VERIFICATO empiricamente

Reverse-engineering del traffico HTTPS dell'app HikConnect iOS via
mitmproxy (Reqable + SSL decryption) sull'hardware reale:

- **DS-KV7413EY-IME2** (outdoor station)
- **DS-KH7300EY-WTE2** (indoor monitor)
- **DS-KAD7063** (bus gateway)

**Request verificata:**
```
POST /v3/userdevices/v1/isapi
Content-Type: application/x-www-form-urlencoded
apiData=PUT /ISAPI/AccessControl/RemoteControl/door/{n}
apiKey=100044
channelNo={n}
deviceSerial={serial}
method=0
```

### Added
- Nuova strategia **`cloud_verified`** (alias A0) con endpoint verificato
- Metodo `HikvisionEyClient.raw_request_form()` per body form-urlencoded
- Parser doppio-layer: valida sia `meta.code` (cloud) sia
  `ResponseStatus/statusCode` (ISAPI del device)
- Costanti `VERIFIED_APIKEY`, `VERIFIED_APIDATA`, `VERIFIED_CHANNEL_NO`,
  `VERIFIED_METHOD`, `ISAPI_SUCCESS_STATUS_CODES` in `const.py`
- Suite di test dedicata `test_strategy_verified.py` con payload reale
  catturato sul device
- Sezione "Endpoint verificato empiricamente" in README con dump del
  request/response osservato

### Changed
- `cloud_verified` è ora la **prima strategia** in modalità auto,
  seguita da `local`, poi dalle legacy `cloud_a4/a3/a2/a1`
- Le strategie A1–A4 rimangono come fallback per configurazioni hardware
  diverse dal serie EY testato
- Bump manifest.json → 0.2.0
- Bump pyproject.toml → 0.2.0

### Documented
- Endpoint `/v3/userdevices/v1/isapi` (POST) con body form-urlencoded
- `apiKey=100044` come codice interno Hik-Connect per il comando
  `RemoteControl/door`
- Mapping `channelNo` ↔ door index (channel 1 = door/1 = cancelletto
  pedonale nell'installazione di riferimento)
- Response ISAPI `<statusCode>1</statusCode>` = successo,
  `-1` = errore device

## [0.1.0] - 2026-07-02

### Added
- Prima release: scaffold completo integrazione HA (25 file, 5389 righe)
- 4 strategie cloud (A1–A4) basate su reverse-engineering della libreria
  `hikconnect` di Bedřich Tomáš + repo `prescornic/hikconnect_alarm`
- Strategia Local ISAPI in LAN come fallback
- Config flow con UI, options flow, reauth flow
- 6 servizi HA (`open_gate`, `answer_call`, `hangup`, `restart`,
  `refresh`, `reconnect`)
- 6 eventi bus HA
- Piattaforme: `button`, `binary_sensor`, `sensor`, `camera`
- HA Quality Scale: **Gold**
- Test suite (config_flow, coordinator, unlock_strategies, services, client)
