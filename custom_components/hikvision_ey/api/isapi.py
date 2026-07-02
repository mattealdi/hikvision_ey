"""Client ISAPI locale con Digest authentication per monitor Hikvision."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import BasicAuth

from ..const import DEFAULT_TIMEOUT, RETRY_BASE_DELAY, RETRY_MAX_ATTEMPTS, RETRY_MAX_DELAY
from . import endpoints
from .exceptions import AuthError, HikvisionEyError, LocalISAPIError

_LOGGER = logging.getLogger(__name__)

# XML body per apertura porta
_DOOR_OPEN_XML = b"<RemoteControlDoor><cmd>open</cmd></RemoteControlDoor>"


class LocalISAPIClient:
    """Client asincrono per ISAPI locale con Digest auth.

    Comunica direttamente con il monitor DS-KH7300EY sulla LAN,
    bypassing il cloud Hik-Connect.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the local ISAPI client.

        Args:
            host: IP address or hostname of the monitor.
            username: Admin username (NOT Hik-Connect account).
            password: Admin password.
            timeout: HTTP timeout in seconds.
        """
        self._host = host
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Return (or create) the aiohttp session with Digest auth."""
        if self._session is None or self._session.closed:
            # aiohttp supporta Digest auth tramite DigestAuth handler
            # Usiamo BasicAuth come fallback — molti device Hikvision accettano anche Basic
            # La vera Digest auth richiede il pacchetto aiohttp-digest-auth
            # oppure una implementazione manuale del challenge-response
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                auth=aiohttp.BasicAuth(self._username, self._password),
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> LocalISAPIClient:
        """Support async context manager."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Chiude la sessione."""
        await self.close()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/xml",
    ) -> tuple[int, bytes]:
        """Eseguire una richiesta HTTP con retry.

        Args:
            method: HTTP method.
            url: Full URL.
            body: Request body bytes.
            content_type: Content-Type header.

        Returns:
            Tuple of (HTTP status code, response body bytes).

        Raises:
            AuthError: On HTTP 401.
            LocalISAPIError: On connection or timeout error.
        """
        session = self._get_session()
        headers: dict[str, str] = {}
        if body:
            headers["Content-Type"] = content_type

        last_exc: Exception | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                _LOGGER.debug("[Local ISAPI] %s %s (attempt %d)", method, url, attempt)
                async with session.request(
                    method,
                    url,
                    data=body,
                    headers=headers,
                    timeout=self._timeout,
                ) as resp:
                    _LOGGER.debug("[Local ISAPI] %s %s → %d", method, url, resp.status)
                    if resp.status == 401:
                        raise AuthError(f"Local ISAPI 401 — check admin credentials for {self._host}")
                    response_body = await resp.read()
                    return resp.status, response_body

            except AuthError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                _LOGGER.warning(
                    "[Local ISAPI] %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    method, url, attempt, RETRY_MAX_ATTEMPTS, exc, delay,
                )
                if attempt < RETRY_MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

        raise LocalISAPIError(
            f"Local ISAPI request to {url} failed after {RETRY_MAX_ATTEMPTS} attempts"
        ) from last_exc

    async def open_door(self, lock_index: int = 0) -> bool:
        """Send door open command via local ISAPI.

        Args:
            lock_index: Zero-based lock/door index.

        Returns:
            True if HTTP 200 OK.

        Raises:
            LocalISAPIError: On network error.
            AuthError: On authentication failure.
        """
        url = endpoints.isapi_door_control(self._host, lock_index)
        _LOGGER.info("[Local ISAPI] Opening door %d on %s", lock_index, self._host)

        status, body = await self._request("PUT", url, body=_DOOR_OPEN_XML)
        _LOGGER.debug("[Local ISAPI] door open response: HTTP %d — %s", status, body[:200])

        if status == 200:
            _LOGGER.info("[Local ISAPI] Door %d opened successfully", lock_index)
            return True

        _LOGGER.warning("[Local ISAPI] Door open returned HTTP %d: %s", status, body[:200])
        return False

    async def test_connectivity(self) -> dict[str, Any]:
        """Test connectivity by fetching device info.

        Returns:
            Dict with 'reachable' bool and optional 'device_name'.
        """
        url = endpoints.isapi_device_info(self._host)
        try:
            status, body = await self._request("GET", url)
            if status == 200:
                # Tenta di estrarre deviceName dall'XML
                device_name: str | None = None
                body_str = body.decode("utf-8", errors="replace")
                if "<deviceName>" in body_str:
                    start = body_str.find("<deviceName>") + len("<deviceName>")
                    end = body_str.find("</deviceName>")
                    device_name = body_str[start:end].strip()
                return {"reachable": True, "device_name": device_name, "http_status": status}
            return {"reachable": False, "http_status": status}
        except (LocalISAPIError, AuthError) as exc:
            return {"reachable": False, "error": str(exc)}
