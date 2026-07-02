# Hikvision EY — Home Assistant Integration

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![GitHub Release](https://img.shields.io/github/v/release/matteoaldi/hikvision_ey)](https://github.com/matteoaldi/hikvision_ey/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HA Quality Scale: Gold](https://img.shields.io/badge/HA%20Quality%20Scale-Gold-gold.svg)](https://developers.home-assistant.io/docs/integration_quality_scale_index)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Verified on hardware](https://img.shields.io/badge/Unlock%20endpoint-Verified%20on%20real%20device-brightgreen.svg)](#endpoint-verificato-empiricamente)

Integrazione Home Assistant per videocitofoni **Hikvision serie EY** su cloud **Hik-Connect**.

> **⚠️ Disclaimer:** questo progetto è indipendente dal repository
> [`home-assistant-hikconnect`](https://github.com/tomasbedrich/home-assistant-hikconnect)
> di Bedřich Tomáš. Non ne copia il codice — è una riscrittura da zero basata
> sull'analisi della stessa API pubblica Hik-Connect, ottimizzata per i device serie EY.

---

## Cosa fa

- **Apre il cancelletto / porta** tramite **strategia cloud verificata empiricamente** (reverse-engineering dell'app HikConnect iOS) + 4 strategie legacy di fallback + ISAPI locale
- **Rileva chiamate** dal citofono in tempo reale (polling adattivo: 3 s durante ringing, 30 s idle)
- **Espone binary sensor**: campanello premuto, chiamata in corso, online status di ogni device
- **Espone sensor**: firmware, segnale WiFi, stato cloud, ultima chiamata, uptime
- **Camera entity** per stream live RTSP/HLS (se disponibile dal cloud)
- **6 servizi HA** con schema tipizzato: `open_gate`, `answer_call`, `hangup`, `restart`, `refresh`, `reconnect`
- **6 eventi** sul bus HA per automazioni avanzate
- **Config flow** con UI, reauth flow, options flow con sezione ISAPI locale opzionale

---

## Dispositivi testati

| Modello | Ruolo | Note |
|---------|-------|------|
| DS-KV7413EY-IME2 | Pannello esterno | Outdoor station, bus 2-fili |
| DS-KH7300EY-WTE2 | Monitor interno WiFi | ISAPI-native, WiFi |
| DS-KAD7063 | Gateway bus 2-fili | Collega outdoor al bus |

---

## Endpoint verificato empiricamente

A differenza di altre integrazioni Hik-Connect (che assumono endpoint
validi solo per device IP-nativi), **questo progetto ha verificato
empiricamente l'endpoint di unlock sul serie EY** tramite
reverse-engineering del traffico HTTPS dell'app HikConnect iOS
(mitmproxy via Reqable con SSL decryption, luglio 2026).

**Request verificata:**

```http
POST /v3/userdevices/v1/isapi HTTP/1.1
Host: apiieu.hik-connect.com
Content-Type: application/x-www-form-urlencoded

apiData=PUT /ISAPI/AccessControl/RemoteControl/door/1
apiKey=100044
channelNo=1
deviceSerial=<serial>
method=0
```

**Response verificata (HTTP 200, JSON):**

```json
{
  "data": "<?xml ...?><ResponseStatus>
            <requestURL>/ISAPI/AccessControl/RemoteControl/door/1</requestURL>
            <statusCode>1</statusCode>
            <statusString>OK</statusString>
            <subStatusCode>ok</subStatusCode>
          </ResponseStatus>",
  "meta": {"code": 200, "message": "OK"}
}
```

Il parser verifica **entrambi** i layer:
- `meta.code == 200` (cloud OK)
- `ResponseStatus/statusCode ∈ {0, 1}` (device OK)

Entrambi devono essere positivi per confermare l'unlock — questo
modello di validazione doppia è più rigoroso di quello delle
integrazioni esistenti che si fermano allo status HTTP.

**Fonte:** questa strategia (`cloud_verified`) è la **prima** in ordine
auto. Le altre (A1–A4) rimangono come fallback per hardware diverso.

---

## Requisiti

- **Home Assistant 2025.1.0+**
- **Python 3.13+** (incluso in HA 2025.x)
- Account **Hik-Connect** (EU/Asia/USA)
- Dispositivi Hikvision serie EY **associati all'account** e **online** nel cloud

---

## Installazione

### Via HACS (consigliato)

1. In HACS, vai su **Integrations** → menu tre puntini → **Custom repositories**
2. Aggiungi `https://github.com/matteoaldi/hikvision_ey` con categoria `Integration`
3. Cerca "Hikvision EY" e clicca **Download**
4. Riavvia Home Assistant
5. Vai su **Settings → Devices & Services → Add Integration** → cerca "Hikvision EY"

### Installazione manuale

1. Copia la cartella `custom_components/hikvision_ey/` nella tua directory `<config>/custom_components/`
2. Riavvia Home Assistant
3. Aggiungi l'integrazione da **Settings → Devices & Services → Add Integration**

---

## Configurazione

### Passo 1 — Credenziali cloud

Inserisci nel config flow:

| Campo | Descrizione | Default |
|-------|-------------|---------|
| Username | Email/username account Hik-Connect | — |
| Password | Password account Hik-Connect | — |
| Region | Regione server cloud | EU |
| Request timeout | Timeout HTTP in secondi | 15 |

> **Nota regione:** se sei in Europa usa `EU`. L'integrazione gestisce automaticamente il redirect
> `meta.code=1100` anche se sbagli la regione iniziale.

### Passo 2 — ISAPI locale (opzionale, consigliato)

Nella sezione **"Local ISAPI (advanced)"** puoi abilitare il percorso diretto LAN:

| Campo | Descrizione |
|-------|-------------|
| Enable local ISAPI | Abilita percorso LAN diretto (più veloce) |
| Monitor host/IP | IP o hostname del monitor DS-KH7300EY |
| ISAPI username | Username admin del monitor (non account Hik-Connect) |
| ISAPI password | Password admin del monitor |

Per trovare l'IP del monitor: `strumenti router → lista dispositivi WiFi` oppure usa
[SADP Tool](https://www.hikvision.com/en/support/tools/hitools/cl8a14672a33b01c00/).

Per testare: `curl -v --digest -u admin:PASSWORD http://IP_MONITOR/ISAPI/System/deviceInfo`

---

## Nota sulla telecamera

**Dalla v0.3.1 l'integrazione non espone entità `camera`.**

Motivo: il modello **DS-KH7300EY-WTE2** non abilita di fabbrica un server
RTSP raggiungibile dalla LAN, e il cloud Hik-Connect restituisce fino a
50 canali placeholder vuoti che non hanno alcun flusso associato.
Esporli come entità HA riempiva la dashboard di voci "Non disponibile"
senza beneficio pratico.

Se hai una videocamera IP separata (es. una DS-2CD… sulla stessa LAN),
configurala con l'integrazione **Generic Camera** standard di
Home Assistant usando l'URL RTSP diretto della camera.

## Servizi

### `hikvision_ey.open_gate`

Apre il cancelletto o una porta.

```yaml
service: hikvision_ey.open_gate
data:
  device_id: "K1234567"   # serial del dispositivo
  lock_index: 0            # indice lock (default 0)
  strategy: auto           # auto / cloud_a1 / cloud_a2 / cloud_a3 / cloud_a4 / local
```

### `hikvision_ey.answer_call`

Risponde a una chiamata in corso.

```yaml
service: hikvision_ey.answer_call
data:
  device_id: "K1234567"
```

### `hikvision_ey.hangup`

Riaggancia una chiamata in corso.

```yaml
service: hikvision_ey.hangup
data:
  device_id: "K1234567"
```

### `hikvision_ey.restart`

Riavvia un dispositivo remoto.

```yaml
service: hikvision_ey.restart
data:
  device_id: "K1234567"
```

### `hikvision_ey.refresh`

Forza un aggiornamento immediato dei dati dal cloud.

```yaml
service: hikvision_ey.refresh
data: {}
```

### `hikvision_ey.reconnect`

Forza un nuovo login al cloud (utile dopo cambio password).

```yaml
service: hikvision_ey.reconnect
data: {}
```

---

## Eventi

Tutti gli eventi includono `device_id`, `serial`, `timestamp`.

| Evento | Quando | Campi extra |
|--------|--------|-------------|
| `hikvision_ey_doorbell_pressed` | Qualcuno suona | `channel` |
| `hikvision_ey_call_started` | Chiamata iniziata | `channel` |
| `hikvision_ey_call_ended` | Chiamata terminata | `duration` |
| `hikvision_ey_gate_opened` | Cancelletto aperto | `strategy`, `lock_index` |
| `hikvision_ey_unlock_failed` | Apertura fallita | `strategy`, `error` |
| `hikvision_ey_cloud_reconnected` | Cloud riconnesso | — |

### Esempio automazione — apri cancelletto quando suona

```yaml
automation:
  - alias: "Apri cancelletto automaticamente"
    trigger:
      platform: event
      event_type: hikvision_ey_doorbell_pressed
    action:
      service: hikvision_ey.open_gate
      data:
        device_id: "K1234567"
        strategy: auto
```

---

## Troubleshooting

### Il cancelletto non apre

L'integrazione implementa **4 strategie cloud** + **1 locale**. Ecco come funzionano e come debuggare:

#### Strategia A1 (legacy `remote/unlock`)

```
PUT /v3/devconfig/v1/call/{serial}/{ch}/remote/unlock?srcId=1&lockId={i}&userType=0
```

L'endpoint storico. Funziona su device IP-nativi più vecchi (KV81xx, KV61xx).
**Su serie EY** il cloud risponde 200 ma il comando non viene propagato al bus 2-fili.

**Debug:** abilita `logger: custom_components.hikvision_ey: debug` in `configuration.yaml`
e cerca righe `[A1]` nei log.

#### Strategia A2 (userdevices lock)

```
PUT /v3/userdevices/v1/devices/{serial}/lock?lockId={i}
```

Endpoint presente nell'APK Hik-Connect per "smart lock". Può funzionare su monitor recenti.

#### Strategia A3 (relay control)

```
POST /v3/devconfig/v1/call/{serial}/relay?relayId={i}&cmd=1
```

Endpoint per device con uscite relè (come la serie EY su bus 2-fili). Probabile candidato
per DS-KV7413EY-IME2 + DS-KAD7063.

#### Strategia A4 (ISAPI over cloud tunnel)

```
POST /v3/devconfig/v1/isapi/{serial}
Body: <RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>
```

Tunnel ISAPI attraverso il cloud. Meccanismo usato dall'app mobile recente per device
ISAPI-native come la serie EY.

#### Strategia Local (ISAPI diretto LAN)

```
PUT http://{monitor_ip}/ISAPI/AccessControl/RemoteControl/door/{i+1}
```

La più affidabile: bypassa il cloud, latenza 100-300 ms invece di 2-5 s.
Richiede IP fisso del monitor + credenziali admin.

#### Come forzare una strategia specifica

```yaml
service: hikvision_ey.open_gate
data:
  device_id: "K1234567"
  strategy: cloud_a3   # forza solo A3
```

Prova ogni strategia in ordine, osserva i log DEBUG per vedere quale `meta.code` restituisce.

#### Meta codes di risposta

| meta.code | Significato |
|-----------|-------------|
| 200 | Successo |
| 60019 | Successo (variante EY) |
| 60020 | Comando inviato ma non confermato |
| 2003 | Device offline |
| 1013/1014 | Credenziali errate |
| 1015 | CAPTCHA richiesto — fai login nell'app mobile poi riprova |
| 1100 | Redirect regione (gestito automaticamente) |
| 429 | Rate limiting — attendi qualche minuto |

### CAPTCHA (meta.code 1015)

Hik-Connect attiva il CAPTCHA dopo troppi login falliti.
**Soluzione:** apri l'app mobile Hik-Connect, effettua login manuale, poi riavvia l'integrazione HA.

### "Cloud connected" = OFF

Il monitor non è raggiungibile dal cloud. Controlla:
1. Monitor acceso e WiFi connesso
2. Account Hik-Connect con dispositivo associato
3. Nessun firewall blocca le uscite TCP verso `apiieu.hik-connect.com` (EU)

---

## Architettura

```
hikvision_ey/
├── api/
│   ├── client.py          # Client aiohttp con login/refresh/JWT/redirect
│   ├── endpoints.py       # URL builders per ogni endpoint
│   ├── unlock_strategies.py  # Strategie A1-A4 + Local
│   ├── models.py          # Dataclasses tipizzate
│   ├── exceptions.py      # Gerarchia eccezioni
│   └── isapi.py           # Client ISAPI locale Digest auth
├── coordinator.py         # 2 DataUpdateCoordinator (device + call status)
├── config_flow.py         # Config/Options/Reauth flow
├── button.py              # 7 button entities
├── binary_sensor.py       # 6 binary sensor entities
├── sensor.py              # 10 sensor entities
├── camera.py              # Camera entity (RTSP/HLS)
├── services.py            # Registrazione 6 servizi
├── diagnostics.py         # Diagnostica con redact automatico
└── entity.py              # Base class entità
```

---

## Contribuire

1. Fork del repository
2. Crea un branch: `git checkout -b feature/my-feature`
3. Installa dipendenze dev: `pip install -e ".[dev]"`
4. Esegui i test: `pytest`
5. Linting: `ruff check . && black --check .`
6. Pull request

---

## Licenza

MIT — vedi [LICENSE](LICENSE)

**Autore:** Matteo Aldi ([@matteoaldi](https://github.com/matteoaldi))

---

## Note legali

Hikvision® e Hik-Connect® sono marchi registrati di HANGZHOU HIKVISION DIGITAL TECHNOLOGY CO., LTD.
Questo progetto non è affiliato, sponsorizzato o approvato da Hikvision.
L'integrazione utilizza API non documentate pubblicamente; l'utilizzo è a rischio dell'utente.
