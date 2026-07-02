# Hikvision EY — Home Assistant Integration

Integrazione HACS per videocitofoni Hikvision serie EY su cloud Hik-Connect.

## Dispositivi supportati

- **DS-KV7413EY-IME2** — Pannello esterno (outdoor station)
- **DS-KH7300EY-WTE2** — Monitor interno WiFi (indoor monitor)
- **DS-KAD7063** — Gateway bus 2-fili

## Funzionalità

- Apertura cancelletto/porta con 4 strategie cloud + ISAPI locale
- Sensori binari: chiamata in corso, campanello premuto, online status
- Sensori: firmware, segnale WiFi, stato cloud, ultima chiamata
- Camera entity per stream RTSP/HLS
- 6 servizi HA: `open_gate`, `answer_call`, `hangup`, `restart`, `refresh`, `reconnect`
- 6 eventi sul bus HA: doorbell pressed, call started/ended, gate opened, unlock failed, cloud reconnected

## Installazione rapida

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=matteoaldi&repository=hikvision_ey&category=integration)

Oppure aggiungi manualmente questo repository in HACS → Integrations → Custom repositories.
