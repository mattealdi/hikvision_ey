"""Client aiohttp asincrono per l'API Hik-Connect."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
from base64 import urlsafe_b64decode
from typing import Any

import aiohttp

from ..const import (
    HIKCONNECT_CLIENT_TYPE,
    HIKCONNECT_FEATURE_CODE,
    HIKCONNECT_LANG,
    REGION_URLS,
    RETRY_BASE_DELAY,
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_DELAY,
    RETRY_STATUS_CODES,
    DEFAULT_TIMEOUT,
)
from . import endpoints
from .exceptions import (
    AuthError,
    CaptchaRequired,
    DeviceOffline,
    HikvisionEyError,
    InvalidResponse,
    RateLimited,
    RegionRedirect,
)
from .models import CallStatus, CameraInfo, DeviceInfo

_LOGGER = logging.getLogger(__name__)

# XML body per apertura porta via ISAPI-over-cloud
_ISAPI_DOOR_OPEN_XML = b"<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>"


def _mask_token(token: str) -> str:
    """Mascherare un token JWT: mostra solo i primi 3 e gli ultimi 3 caratteri."""
    if len(token) <= 6:
        return "***"
    return f"{token[:3]}...{token[-3:]}"


def _mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Rimuovere campi sensibili dal payload per il logging."""
    sensitive = {"password", "sessionId", "rfSessionId", "refreshSessionId"}
    return {
        k: "***" if k in sensitive else v
        for k, v in payload.items()
    }


class HikvisionEyClient:
    """Client asincrono per l'API Hik-Connect.

    Gestisce login, refresh automatico del token, redirect regione e retry
    con exponential backoff.
    """

    def __init__(
        self,
        region: str = "EU",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        Args:
            region: Cloud region ('EU', 'Asia', 'USA').
            timeout: HTTP request timeout in seconds.
        """
        self._region = region
        self._base_url = REGION_URLS.get(region, REGION_URLS["EU"])
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

        # Stato sessione
        self._session_id: str | None = None
        self._refresh_session_id: str | None = None
        self.login_valid_until: datetime.datetime | None = None

    # ── Session management ─────────────────────────────────────────────────────

    def _get_session(self) -> aiohttp.ClientSession:
        """Return (or create) the underlying aiohttp session."""
        if self._session is None or self._session.closed:
            headers = {
                "clientType": HIKCONNECT_CLIENT_TYPE,
                "lang": HIKCONNECT_LANG,
                "featureCode": HIKCONNECT_FEATURE_CODE,
            }
            if self._session_id:
                headers["sessionId"] = self._session_id
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> HikvisionEyClient:
        """Support async context manager."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Chiude la sessione all'uscita dal context manager."""
        await self.close()

    # ── HTTP helpers ───────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
        skip_session_id: bool = False,
    ) -> dict[str, Any]:
        """Eseguire una richiesta HTTP con retry su errori transitori.

        Args:
            method: HTTP method ('GET', 'PUT', 'POST').
            url: Full URL.
            data: Form data (application/x-www-form-urlencoded).
            json_body: JSON body (application/json).
            headers: Additional headers.
            skip_session_id: If True, omit sessionId from request headers.

        Returns:
            Parsed JSON response body.

        Raises:
            AuthError: On HTTP 401 or meta.code 1013/1014.
            CaptchaRequired: On meta.code 1015.
            RateLimited: On HTTP 429.
            HikvisionEyError: On other unrecoverable errors.
        """
        session = self._get_session()
        req_headers = dict(session.headers)  # copy base headers
        if skip_session_id:
            req_headers.pop("sessionId", None)
        if headers:
            req_headers.update(headers)

        last_exc: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                _LOGGER.debug("[HTTP] %s %s (attempt %d/%d)", method, url, attempt, RETRY_MAX_ATTEMPTS)
                async with session.request(
                    method,
                    url,
                    data=data,
                    json=json_body,
                    headers=req_headers,
                    timeout=self._timeout,
                ) as resp:
                    _LOGGER.debug("[HTTP] %s %s → status=%d", method, url, resp.status)

                    if resp.status == 401:
                        raise AuthError("HTTP 401 Unauthorized — session may have expired")

                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 60))
                        raise RateLimited(retry_after)

                    if resp.status in RETRY_STATUS_CODES:
                        # Errore transitorio: ritenta con backoff
                        raise aiohttp.ClientResponseError(
                            resp.request_info,
                            resp.history,
                            status=resp.status,
                        )

                    try:
                        body = await resp.json(content_type=None)
                    except (json.JSONDecodeError, aiohttp.ContentTypeError) as exc:
                        raise InvalidResponse(f"Non-JSON response from {url}") from exc

                    _LOGGER.debug("[HTTP] %s %s — payload: %s", method, url, _mask_payload(body) if isinstance(body, dict) else body)
                    return body  # type: ignore[return-value]

            except (AuthError, CaptchaRequired, RateLimited, InvalidResponse):
                # Errori non-retriable
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                _LOGGER.warning(
                    "[HTTP] %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    method, url, attempt, RETRY_MAX_ATTEMPTS, exc, delay,
                )
                if attempt < RETRY_MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

        raise HikvisionEyError(f"Request to {url} failed after {RETRY_MAX_ATTEMPTS} attempts") from last_exc

    # ── Auth ───────────────────────────────────────────────────────────────────

    async def login(self, username: str, password: str, *, _depth: int = 0) -> None:
        """Login to Hik-Connect cloud.

        Args:
            username: Account username or email.
            password: Account password (hashed to MD5 before sending).
            _depth: Internal recursion depth for region redirect handling.

        Raises:
            AuthError: On wrong credentials.
            CaptchaRequired: If CAPTCHA is required.
            RegionRedirect: If redirected to a different region (handled internally).
        """
        if _depth > 2:
            raise AuthError("Too many region redirects during login")

        pw_hash = hashlib.md5(password.encode("utf-8")).hexdigest()  # noqa: S324
        body = {
            "account": username,
            "password": pw_hash,
        }

        # Login non deve inviare sessionId
        resp = await self._request(
            "POST",
            endpoints.login(self._base_url),
            data=body,
            skip_session_id=True,
        )

        meta_code = resp.get("meta", {}).get("code")

        if meta_code in (1013, 1014):
            raise AuthError("Login failed: wrong username or password")

        if meta_code == 1015:
            raise CaptchaRequired(
                "CAPTCHA required — please login via Hik-Connect mobile app and retry"
            )

        if meta_code == 1100:
            # Redirect a diversa regione
            new_domain = resp.get("loginArea", {}).get("apiDomain")
            if not new_domain:
                raise AuthError("Region redirect without apiDomain in response")
            new_base = f"https://{new_domain}"
            _LOGGER.info("[Auth] Region redirect → %s", new_base)
            self._base_url = new_base
            # Ricrea la sessione senza sessionId vecchio
            await self.close()
            await self.login(username, password, _depth=_depth + 1)
            return

        try:
            session_id = resp["loginSession"]["sessionId"]
            refresh_session_id = resp["loginSession"]["rfSessionId"]
        except KeyError as exc:
            raise AuthError(f"Unexpected login response structure: {exc}") from exc

        self._apply_session(session_id, refresh_session_id)
        _LOGGER.info("[Auth] Login successful (masked token: %s)", _mask_token(session_id))

    async def refresh_login(self) -> None:
        """Refresh the current session using refreshSessionId.

        Raises:
            AuthError: If refresh fails (token expired, relogin required).
        """
        if not self._refresh_session_id:
            raise AuthError("No refresh session ID available — full login required")

        body = {
            "refreshSessionId": self._refresh_session_id,
            "featureCode": HIKCONNECT_FEATURE_CODE,
        }
        resp = await self._request(
            "PUT",
            endpoints.refresh_login(self._base_url),
            data=body,
            skip_session_id=True,
        )

        try:
            session_id = resp["sessionInfo"]["sessionId"]
            refresh_session_id = resp["sessionInfo"]["refreshSessionId"]
        except KeyError as exc:
            raise AuthError(f"Unexpected refresh response structure: {exc}") from exc

        self._apply_session(session_id, refresh_session_id)
        _LOGGER.info("[Auth] Token refreshed (masked: %s)", _mask_token(session_id))

    def _apply_session(self, session_id: str, refresh_session_id: str) -> None:
        """Apply new session IDs and decode JWT expiration."""
        self._session_id = session_id
        self._refresh_session_id = refresh_session_id
        self.login_valid_until = self._decode_jwt_expiration(session_id)

        # Aggiorna header della sessione esistente
        if self._session and not self._session.closed:
            self._session.headers.update({"sessionId": session_id})
        else:
            # La sessione sarà ricreata con il nuovo header alla prossima richiesta
            pass

        _LOGGER.debug(
            "[Auth] Session valid until %s (masked: %s)",
            self.login_valid_until,
            _mask_token(session_id),
        )

    def needs_token_refresh(self) -> bool:
        """Return True if token should be refreshed (expires in < 1 hour)."""
        if not self.login_valid_until:
            return True
        remaining = self.login_valid_until - datetime.datetime.now()
        return remaining < datetime.timedelta(hours=1)

    async def ensure_authenticated(self, username: str, password: str) -> None:
        """Ensure session is valid, refreshing or re-logging if needed.

        Args:
            username: Account username (for re-login if refresh fails).
            password: Account password (for re-login if refresh fails).
        """
        if not self.needs_token_refresh():
            return
        try:
            await self.refresh_login()
        except AuthError:
            _LOGGER.warning("[Auth] Refresh failed, attempting full re-login")
            await self.login(username, password)

    @staticmethod
    def _decode_jwt_expiration(jwt: str) -> datetime.datetime:
        """Decode JWT expiration without external dependencies.

        Args:
            jwt: JWT token string.

        Returns:
            Expiration datetime (local time).
        """
        parts = jwt.split(".")
        if len(parts) < 2:
            # JWT non valido — restituiamo scadenza immediata per forzare refresh
            return datetime.datetime.now()
        payload_b64 = parts[1]
        # Padding base64
        padding_needed = len(payload_b64) % 4
        if padding_needed:
            payload_b64 += "=" * (4 - padding_needed)
        try:
            claims = json.loads(urlsafe_b64decode(payload_b64))
            return datetime.datetime.fromtimestamp(claims["exp"])
        except (KeyError, ValueError):
            return datetime.datetime.now()

    # ── Devices ────────────────────────────────────────────────────────────────

    async def get_devices(self) -> list[DeviceInfo]:
        """Fetch all devices associated with the current account.

        Returns:
            List of DeviceInfo dataclasses.
        """
        devices: list[DeviceInfo] = []
        limit = 50
        offset = 0
        has_next = True

        while has_next:
            resp = await self._request(
                "GET",
                endpoints.device_pagelist(self._base_url, limit=limit, offset=offset),
            )
            _LOGGER.debug("[Devices] Got page offset=%d, count=%d", offset, len(resp.get("deviceInfos", [])))

            for raw_device in resp.get("deviceInfos", []):
                device = self._parse_device(raw_device, resp)
                # Recupera info camera
                cameras = await self.get_cameras(device.serial)
                device.cameras = cameras
                devices.append(device)

            offset += limit
            has_next = resp.get("page", {}).get("hasNext", False)

        return devices

    @staticmethod
    def _parse_device(device: dict[str, Any], resp: dict[str, Any]) -> DeviceInfo:
        """Parse a device entry from the pagelist response."""
        serial = device["deviceSerial"]
        conn = (resp.get("connectionInfos") or {}).get(serial) or {}
        status = (resp.get("statusInfos") or {}).get(serial) or {}
        wifi = (resp.get("wifiInfos") or {}).get(serial) or {}

        is_online = HikvisionEyClient._parse_is_online(status)
        local_ip: str | None = HikvisionEyClient._clean_ip(conn.get("localIp")) or HikvisionEyClient._clean_ip(wifi.get("address"))
        wan_ip: str | None = HikvisionEyClient._clean_ip(conn.get("netIp"))
        wifi_signal: int | None = wifi.get("signal") if isinstance(wifi.get("signal"), int) else None

        # v0.5.0: azzeriamo IP/segnale SOLO se offline ESPLICITO (False).
        # None (sconosciuto) non implica offline, quindi non tocchiamo i dati.
        if is_online is False:
            local_ip = wan_ip = wifi_signal = None

        return DeviceInfo(
            full_serial=device["fullSerial"],
            serial=serial,
            name=device["name"],
            device_type=device.get("deviceType", ""),
            firmware_version=device.get("version", ""),
            is_online=is_online,
            local_ip=local_ip,
            wan_ip=wan_ip,
            wifi_signal=wifi_signal,
            update_available=HikvisionEyClient._parse_update_available(status),
            locks=HikvisionEyClient._parse_locks(status),
            raw=device,
            cloud_is_online=is_online,
            online_source="cloud",
        )

    @staticmethod
    def _clean_ip(value: Any) -> str | None:
        """Return IP string or None if empty/invalid."""
        if not isinstance(value, str) or not value or value == "0.0.0.0":
            return None
        return value

    @staticmethod
    def _parse_is_online(status: dict[str, Any]) -> bool | None:
        """Parse online status from statusInfos entry."""
        code = status.get("globalStatus")
        return (code == 1) if code is not None else None

    @staticmethod
    def _parse_update_available(status: dict[str, Any]) -> bool | None:
        """Parse firmware update availability."""
        value = status.get("upgradeAvailable")
        return bool(value) if value is not None else None

    @staticmethod
    def _parse_locks(status: dict[str, Any]) -> dict[int, int]:
        """Parse lock number map from statusInfos optionals."""
        try:
            locks_json = json.loads(status["optionals"]["lockNum"])
            return {int(k): int(v) for k, v in locks_json.items()}
        except (KeyError, ValueError, json.JSONDecodeError):
            return {}

    async def get_cameras(self, device_serial: str) -> list[CameraInfo]:
        """Fetch camera info for a device.

        Args:
            device_serial: Short serial number.

        Returns:
            List of CameraInfo dataclasses.
        """
        resp = await self._request(
            "GET",
            endpoints.cameras_info(self._base_url, device_serial),
        )
        cameras = []
        for cam in resp.get("cameraInfos", []):
            cameras.append(
                CameraInfo(
                    camera_id=cam["cameraId"],
                    name=cam["cameraName"],
                    channel_number=cam["channelNo"],
                    signal_status=cam.get("deviceChannelInfo", {}).get("signalStatus", 0),
                    is_shown=bool(cam.get("isShow", True)),
                )
            )
        return cameras

    # ── Call status ────────────────────────────────────────────────────────────

    async def get_call_status(self, serial: str) -> CallStatus:
        """Fetch current call/ringing status for a device.

        Args:
            serial: Short device serial.

        Returns:
            CallStatus dataclass.

        Raises:
            DeviceOffline: If meta.code == 2003.
        """
        resp = await self._request("GET", endpoints.call_status(self._base_url, serial))

        meta_code = resp.get("meta", {}).get("code")
        if meta_code == 2003:
            raise DeviceOffline(serial)

        # Il payload può essere una stringa JSON o un dict
        raw_data = resp.get("data", "{}")
        if isinstance(raw_data, str):
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = {}
        else:
            data = raw_data

        # Supporta sia formato legacy (callStatus int) sia formato ISAPI (CallerInfo.status)
        if "callStatus" in data:
            from ..const import CALL_STATUS_MAPPING
            status_code = data["callStatus"]
            status = CALL_STATUS_MAPPING.get(status_code, "unknown")
        elif "CallerInfo" in data:
            # Formato ISAPI-native (PR #65 ref)
            status_str = data["CallerInfo"].get("status", "idle")
            status = status_str.lower()
        else:
            status = "idle"

        caller = data.get("callerInfo") or data.get("CallerInfo") or {}
        return CallStatus(
            status=status,
            building_number=caller.get("buildingNo"),
            floor_number=caller.get("floorNo"),
            zone_number=caller.get("zoneNo"),
            unit_number=caller.get("unitNo"),
            device_number=caller.get("devNo"),
            device_type=caller.get("devType"),
            lock_number=caller.get("lockNum"),
        )

    # ── Call operations ────────────────────────────────────────────────────────

    async def answer_call(self, serial: str) -> None:
        """Send answer call command (cmdId=2)."""
        await self._request("PUT", endpoints.call_operation(self._base_url, serial, 2))
        _LOGGER.info("[Call] Answer sent to device %s", serial)

    async def cancel_call(self, serial: str) -> None:
        """Send cancel call command (cmdId=3)."""
        await self._request("PUT", endpoints.call_operation(self._base_url, serial, 3))
        _LOGGER.info("[Call] Cancel sent to device %s", serial)

    async def hangup_call(self, serial: str) -> None:
        """Send hangup call command (cmdId=5)."""
        await self._request("PUT", endpoints.call_operation(self._base_url, serial, 5))
        _LOGGER.info("[Call] Hangup sent to device %s", serial)

    # ── Device management ──────────────────────────────────────────────────────

    async def restart_device(self, serial: str) -> None:
        """Send restart command to a device."""
        await self._request("PUT", endpoints.device_restart(self._base_url, serial))
        _LOGGER.info("[Device] Restart sent to device %s", serial)

    # ── Low-level HTTP (esposto per le unlock strategies) ──────────────────────

    async def raw_request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        xml_body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a raw API request (used by unlock strategies).

        Args:
            method: HTTP method.
            url: Full URL.
            data: Form data.
            xml_body: Raw XML bytes body.
            extra_headers: Additional headers.

        Returns:
            Parsed JSON response.
        """
        if xml_body is not None:
            extra_headers = {**(extra_headers or {}), "Content-Type": "application/xml"}
            return await self._request(
                method, url, json_body=None, headers=extra_headers, data=None
            )
        return await self._request(method, url, data=data, headers=extra_headers)

    async def raw_request_xml(
        self,
        method: str,
        url: str,
        xml_body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a request with XML body and return JSON response.

        Used by Strategy A4 (ISAPI-over-cloud-tunnel).
        """
        merged_headers: dict[str, str] = {"Content-Type": "application/xml"}
        if extra_headers:
            merged_headers.update(extra_headers)

        session = self._get_session()
        req_headers = {**dict(session.headers), **merged_headers}

        last_exc: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                _LOGGER.debug("[HTTP] %s %s (XML, attempt %d)", method, url, attempt)
                async with session.request(
                    method,
                    url,
                    data=xml_body,
                    headers=req_headers,
                    timeout=self._timeout,
                ) as resp:
                    _LOGGER.debug("[HTTP] %s %s → status=%d", method, url, resp.status)
                    if resp.status == 401:
                        raise AuthError("HTTP 401 on XML request")
                    if resp.status == 404:
                        # Usiamo un dict speciale per segnalare 404
                        return {"_http_status": 404, "meta": {"code": 404}}
                    if resp.status in RETRY_STATUS_CODES:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    try:
                        body = await resp.json(content_type=None)
                    except (json.JSONDecodeError, aiohttp.ContentTypeError):
                        # Risposta XML da tunnel ISAPI: considera successo se HTTP 200
                        return {"_http_status": resp.status, "meta": {"code": 200 if resp.status == 200 else resp.status}}
                    return body  # type: ignore[return-value]
            except (AuthError,):
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                if attempt < RETRY_MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

        raise HikvisionEyError(f"XML request to {url} failed after {RETRY_MAX_ATTEMPTS} attempts") from last_exc

    async def raw_request_form(
        self,
        method: str,
        url: str,
        form: dict[str, str],
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a form-urlencoded request (usato dalla strategia cloud_verified).

        Il tunnel ISAPI di Hik-Connect si aspetta un body
        application/x-www-form-urlencoded con i campi apiData/apiKey/
        channelNo/deviceSerial/method. La response è sempre JSON con
        il payload ISAPI XML dentro il campo `data`.

        Args:
            method: Metodo HTTP (POST).
            url: URL completo.
            form: Dizionario dei campi form.
            extra_headers: Header addizionali.

        Returns:
            Dict con la response JSON parsata + campo interno `_http_status`.
        """
        merged_headers: dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if extra_headers:
            merged_headers.update(extra_headers)

        session = self._get_session()
        req_headers = {**dict(session.headers), **merged_headers}

        last_exc: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                _LOGGER.debug(
                    "[HTTP] %s %s (form, attempt %d, fields=%s)",
                    method,
                    url,
                    attempt,
                    list(form.keys()),
                )
                async with session.request(
                    method,
                    url,
                    data=form,
                    headers=req_headers,
                    timeout=self._timeout,
                ) as resp:
                    _LOGGER.debug("[HTTP] %s %s → status=%d", method, url, resp.status)
                    if resp.status == 401:
                        raise AuthError("HTTP 401 on form request")
                    if resp.status in RETRY_STATUS_CODES:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status
                        )
                    try:
                        body = await resp.json(content_type=None)
                        if isinstance(body, dict):
                            body["_http_status"] = resp.status
                            return body
                        return {"_http_status": resp.status, "raw": body}
                    except (json.JSONDecodeError, aiohttp.ContentTypeError):
                        text = await resp.text()
                        _LOGGER.debug("[HTTP] Non-JSON response: %s", text[:200])
                        return {
                            "_http_status": resp.status,
                            "raw": text,
                            "meta": {"code": 200 if resp.status == 200 else resp.status},
                        }
            except (AuthError,):
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                if attempt < RETRY_MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

        raise HikvisionEyError(
            f"Form request to {url} failed after {RETRY_MAX_ATTEMPTS} attempts"
        ) from last_exc

    @property
    def base_url(self) -> str:
        """Return current base URL (may change after region redirect)."""
        return self._base_url
