"""Costanti per l'integrazione Hikvision EY."""
from __future__ import annotations

DOMAIN = "hikvision_ey"
MANUFACTURER = "Hikvision"
ATTRIBUTION = "Data provided by Hik-Connect cloud"

# Piattaforme HA abilitate
# NOTE v0.3.1: piattaforma 'camera' rimossa. Il modello DS-KH7300EY-WTE2
# non espone RTSP e i canali riportati dal cloud sono 50 placeholder vuoti.
PLATFORMS: list[str] = [
    "binary_sensor",
    "button",
    "sensor",
]

# ── Chiavi configurazione ──────────────────────────────────────────────────────
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_REGION = "region"
CONF_TIMEOUT = "timeout"

# Sezione ISAPI locale (opzionale)
CONF_LOCAL_ISAPI_ENABLED = "local_isapi_enabled"
CONF_LOCAL_HOST = "local_host"
CONF_LOCAL_USERNAME = "local_username"
CONF_LOCAL_PASSWORD = "local_password"

# Opzioni avanzate (scritte in entry.options dall'OptionsFlow o dall'auto-strategy)
CONF_PREFERRED_STRATEGY = "preferred_strategy"
CONF_DEVICE_POLL_INTERVAL = "device_poll_interval"
CONF_CALL_POLL_INTERVAL_IDLE = "call_poll_interval_idle"
CONF_CALL_POLL_INTERVAL_RINGING = "call_poll_interval_ringing"

# ── Valori di default ─────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 15  # secondi
DEFAULT_REGION = "EU"
DEFAULT_DEVICE_POLL_INTERVAL = 300  # 5 minuti
DEFAULT_CALL_POLL_INTERVAL_IDLE = 30  # secondi
DEFAULT_CALL_POLL_INTERVAL_RINGING = 3  # secondi durante ringing

# ── Regioni e URL base ────────────────────────────────────────────────────────
REGION_URLS: dict[str, str] = {
    "EU": "https://apiieu.hik-connect.com",
    "Asia": "https://api.hik-connect.com",
    "USA": "https://apiusa.hik-connect.com",
}

REGION_LIST: list[str] = list(REGION_URLS.keys())

# ── Strategie unlock ──────────────────────────────────────────────────────────
STRATEGY_AUTO = "auto"
STRATEGY_CLOUD_VERIFIED = "cloud_verified"  # A0 — ISAPI tunnel via userdevices, verificato empiricamente
STRATEGY_CLOUD_A1 = "cloud_a1"
STRATEGY_CLOUD_A2 = "cloud_a2"
STRATEGY_CLOUD_A3 = "cloud_a3"
STRATEGY_CLOUD_A4 = "cloud_a4"
STRATEGY_LOCAL = "local"

STRATEGY_LIST: list[str] = [
    STRATEGY_AUTO,
    STRATEGY_CLOUD_VERIFIED,
    STRATEGY_CLOUD_A1,
    STRATEGY_CLOUD_A2,
    STRATEGY_CLOUD_A3,
    STRATEGY_CLOUD_A4,
    STRATEGY_LOCAL,
]

# Meta codes cloud considerati "successo"
UNLOCK_SUCCESS_CODES: set[int] = {200, 60019}

# ── Endpoint verificato empiricamente (strategia cloud_verified) ─────────────
# Verificato su DS-KV7413EY-IME2 + DS-KH7300EY-WTE2 + DS-KAD7063 tramite mitmproxy
# (Reqable iOS con SSL decryption) — luglio 2026.
# La response ISAPI restituisce <statusCode>1</statusCode><statusString>OK</statusString>
# racchiuso in JSON {"data": "<xml>...</xml>", "meta": {"code": 200}}.
VERIFIED_APIKEY = "100044"                       # codice interno Hik-Connect per RemoteControl door
VERIFIED_APIDATA = "PUT /ISAPI/AccessControl/RemoteControl/door/1"
VERIFIED_CHANNEL_NO = "1"                         # channelNo del cancelletto pedonale (door/1)
VERIFIED_METHOD = "0"                             # method observed nel traffico app

# ISAPI response payload considerato successo (parsando il campo data)
ISAPI_SUCCESS_STATUS_CODES: set[int] = {0, 1}    # 0 e 1 = OK nei ResponseStatus Hikvision

# ── Nomi eventi sul bus HA ────────────────────────────────────────────────────
EVENT_DOORBELL_PRESSED = f"{DOMAIN}_doorbell_pressed"
EVENT_CALL_STARTED = f"{DOMAIN}_call_started"
EVENT_CALL_ENDED = f"{DOMAIN}_call_ended"
EVENT_GATE_OPENED = f"{DOMAIN}_gate_opened"
EVENT_UNLOCK_FAILED = f"{DOMAIN}_unlock_failed"
EVENT_CLOUD_RECONNECTED = f"{DOMAIN}_cloud_reconnected"

EVENTS: list[str] = [
    EVENT_DOORBELL_PRESSED,
    EVENT_CALL_STARTED,
    EVENT_CALL_ENDED,
    EVENT_GATE_OPENED,
    EVENT_UNLOCK_FAILED,
    EVENT_CLOUD_RECONNECTED,
]

# ── HTTP / retry ──────────────────────────────────────────────────────────────
RETRY_STATUS_CODES: set[int] = {429, 500, 502, 503, 504}
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # secondi
RETRY_MAX_DELAY = 30.0  # secondi

# ── Hik-Connect API ───────────────────────────────────────────────────────────
HIKCONNECT_CLIENT_TYPE = "55"
HIKCONNECT_FEATURE_CODE = "deadbeef"
HIKCONNECT_LANG = "en-US"

# Call status mapping (legacy format)
CALL_STATUS_IDLE = 1
CALL_STATUS_RINGING = 2
CALL_STATUS_IN_CALL = 3

CALL_STATUS_MAPPING: dict[int, str] = {
    CALL_STATUS_IDLE: "idle",
    CALL_STATUS_RINGING: "ringing",
    CALL_STATUS_IN_CALL: "in_call",
}
