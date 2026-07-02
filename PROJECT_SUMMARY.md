# PROJECT_SUMMARY — hikvision_ey v0.1.0

**Autore:** Matteo Aldi  
**Data scaffolding:** 2026-07-02  
**Target:** Home Assistant 2025.x, Python 3.13, HACS, Quality Scale GOLD

---

## File creati (25 Python + 12 altri)

```
hikvision_ey/
├── README.md                            (335 righe — documentazione completa)
├── LICENSE                              (MIT)
├── pyproject.toml                       (76 righe — ruff + black + pytest config)
├── .gitignore
├── hacs.json
├── info.md
├── .github/workflows/
│   ├── validate.yml                     (hassfest + HACS validate)
│   └── test.yml                         (ruff + black + pytest + coverage)
├── custom_components/hikvision_ey/
│   ├── __init__.py                      (111 righe — setup_entry, unload, listeners)
│   ├── manifest.json                    (gold quality_scale, cloud_polling, no requirements)
│   ├── const.py                         (107 righe — DOMAIN, PLATFORMS, REGION_URLS, EVENTS, strategie)
│   ├── config_flow.py                   (370 righe — user step, reauth, options flow + ISAPI locale)
│   ├── coordinator.py                   (309 righe — DeviceCoordinator + CallStatusCoordinator adattivo)
│   ├── entity.py                        (75 righe — HikvisionEyEntity base class)
│   ├── services.py                      (258 righe — 6 servizi con schema voluptuous)
│   ├── services.yaml                    (87 righe — descrizione servizi per UI HA)
│   ├── diagnostics.py                   (102 righe — async_get_config_entry_diagnostics + TO_REDACT)
│   ├── strings.json                     (151 righe — stringhe base EN)
│   ├── binary_sensor.py                 (202 righe — 6 binary sensor)
│   ├── button.py                        (187 righe — 7 button)
│   ├── camera.py                        (150 righe — Camera entity con RTSP stub)
│   ├── sensor.py                        (202 righe — 10 sensor)
│   ├── api/
│   │   ├── __init__.py                  (39 righe — esporta tutto)
│   │   ├── client.py                    (652 righe — HikvisionEyClient completo)
│   │   ├── endpoints.py                 (133 righe — URL builders per ogni endpoint)
│   │   ├── exceptions.py                (65 righe — gerarchia eccezioni)
│   │   ├── isapi.py                     (183 righe — LocalISAPIClient Digest auth)
│   │   ├── models.py                    (97 righe — DeviceInfo, CameraInfo, CallStatus, UnlockResult)
│   │   └── unlock_strategies.py         (409 righe — StrategyA1..A4 + Local + UnlockManager)
│   └── translations/
│       ├── en.json                      (175 righe)
│       └── it.json                      (175 righe)
└── tests/
    ├── __init__.py
    ├── conftest.py                      (236 righe — fixtures completi)
    ├── test_client.py                   (255 righe — login, refresh, JWT, get_devices, call_status)
    ├── test_config_flow.py              (325 righe — happy path + errori + reauth + options)
    ├── test_coordinator.py              (271 righe — device + call coordinator, eventi, intervalli)
    ├── test_unlock_strategies.py        (362 righe — ogni strategia con 200/60019/fallito/offline/eccezione)
    └── test_services.py                 (288 righe — open_gate, hangup, refresh, reconnect, register)
```

**Totale Python:** 5.389 righe  
**Totale file:** 37

---

## Come installare (per test immediato)

### Opzione A — HACS custom repository (quando pubblicato)
1. HACS → Integrations → ⋮ → Custom repositories
2. URL: `https://github.com/matteoaldi/hikvision_ey` / Category: Integration
3. Scarica e riavvia HA

### Opzione B — Installazione manuale (ADESSO)
```bash
# Sul tuo sistema HA (o copia manuale via Samba/SSH)
cp -r custom_components/hikvision_ey /config/custom_components/
# Riavvia HA
```

### Opzione C — Sviluppo locale con devcontainer
```bash
# Installa dipendenze dev
pip install -e ".[dev]"
# Esegui i test
pytest tests/ -v
```

---

## Cosa testare per primo (ordine priorità)

### 1. Verifica config flow (5 minuti)
- HA → Settings → Devices & Services → Add Integration → cerca "Hikvision EY"
- Inserisci credenziali Hik-Connect (EU region)
- Verifica che il redirect 1100 venga gestito automaticamente (region EU → apiieu)

### 2. Verifica apertura cancelletto (10 minuti)
```yaml
# In Developer Tools → Services
service: hikvision_ey.open_gate
data:
  device_id: "IL_TUO_SERIAL"  # lo vedi nei sensor entities
  strategy: cloud_a4           # prova prima A4 (ISAPI tunnel)
```
Osserva i log DEBUG (attiva `logger: custom_components.hikvision_ey: debug` in `configuration.yaml`).

**Strategia da provare in ordine:**
1. `cloud_a4` — più probabile per serie EY (ISAPI tunnel)
2. `cloud_a3` — relay control per bus 2-fili
3. `cloud_a2` — smart lock endpoint
4. `cloud_a1` — legacy (probabilmente non funziona su EY)

### 3. Verifica campanello (passivo)
- Premi il campanello fisicamente
- Controlla che `binary_sensor.X_doorbell_ringing` passi a `on`
- Controlla che arrivi l'evento `hikvision_ey_doorbell_pressed` nel log

### 4. Attiva ISAPI locale (se hai IP del monitor)
- Options flow → abilita "Local ISAPI"
- Inserisci IP del DS-KH7300EY-WTE2
- Credenziali admin del monitor (non Hik-Connect!)
- Testa con: `curl -v --digest -u admin:PASSWORD http://IP/ISAPI/System/deviceInfo`

---

## Note importanti per Matteo

### Meta code da osservare nei log
Quando esegui `open_gate`, cerca nei log righe come:
```
[A4] meta.code=200, http_status=200 → success=True
```
o
```
[A3] Response meta.code=60019 → success=True
```
Quello che trovi indica quale strategia funziona sul tuo impianto.

### Se vedi meta.code=2003
Il dispositivo è offline sul cloud. Verifica che il monitor DS-KH7300EY-WTE2 
sia acceso e connesso al WiFi.

### Se vedi meta.code=1015 (CAPTCHA)
Apri l'app Hik-Connect sul telefono, fai login manuale, poi riavvia l'integrazione HA.

### Se vedi meta.code=60020
Il cloud dice "comando inviato" ma non confermato. Solitamente succede con A1 su EY.
L'integrazione lo tratta come **fallimento** (solo 200 e 60019 sono successo).

### Strategia vincente → salvata automaticamente
Quando una strategia funziona, viene salvata in `entry.options['preferred_strategy']`
e usata di default da quel momento. Non devi configurarla manualmente.

### Camera entity
La Camera entity è uno stub preparatorio per futuro supporto RTSP/HLS via cloud tunnel.
Per ora mostra `unavailable` perché lo stream cloud P2P non è documentato pubblicamente.
Per live view, configurare stream RTSP locale nel Camera generico di HA:
```yaml
camera:
  - platform: generic
    still_image_url: http://IP_MONITOR/ISAPI/Streaming/channels/101/picture
    stream_source: rtsp://admin:PASSWORD@IP_MONITOR:554/Streaming/Channels/101
```

### Test suite
```bash
# Sintassi già verificata: tutti i 25 .py file — OK
python3 -m py_compile custom_components/hikvision_ey/**/*.py

# Per eseguire i test (richiede homeassistant installato):
pip install -e ".[dev]"
pytest tests/ -v --tb=short
```

---

## Architettura tecnica — decisioni chiave

| Decisione | Motivazione |
|-----------|-------------|
| 4 strategie cloud + Local in UnlockManager | L'endpoint A1 legacy non funziona su EY; A4 (ISAPI tunnel) è il probabile vincitore |
| 2 coordinator separati | Poll device ogni 300s, call status ogni 3s in ringing vs 30s idle |
| Strategy memorizzata in entry.options | Evita di riprovare strategie fallite ad ogni apertura |
| No Lock entity (solo Button) | Lock in HA richiede `supported_features` dichiarato, Button è più diretto per "apri" |
| aiohttp nativo (no requirements) | Evita dipendenze esterne che possono conflittare con HA; aiohttp è già incluso |
| JWT decode manuale | Evita dipendenza PyJWT che aveva incompatibilità con HA (nota dal repo Bedřich) |
| CAPTCHA → ConfigEntryAuthFailed | Forza reauth flow, non retry loop che aggraverebbe il rate limit |
| meta.code 200 E 60019 come successo | 60019 è variante nota di "success" su device EY (dalla community HA) |

---

*Generato da Perplexity Computer — scaffolding automatico basato su PHASE1_ANALYSIS.md*
