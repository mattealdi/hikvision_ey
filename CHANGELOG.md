# Changelog

Tutte le modifiche rilevanti al progetto sono documentate qui.
Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).

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
