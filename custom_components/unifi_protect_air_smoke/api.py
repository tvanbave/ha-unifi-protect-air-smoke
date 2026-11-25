"""Small client for the UniFi OS / Protect private API.

Read-only: it logs in with a local UniFi OS account, reads the bootstrap,
the sensor list and recent events, and streams the Protect update WebSocket.
Parsing is deliberately tolerant: unknown fields and values are ignored
instead of failing the whole update, so firmware changes degrade gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import logging
import struct
import time
from typing import Any
import zlib

import aiohttp

_LOGGER = logging.getLogger(__name__)

_LOGIN_PATH = "/api/auth/login"
_BOOTSTRAP_PATH = "/proxy/protect/api/bootstrap"
_SENSORS_PATH = "/proxy/protect/api/sensors"
_EVENTS_PATH = "/proxy/protect/api/events"
_WS_PATH = "/proxy/protect/ws/updates"

_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
_WS_HEADER = struct.Struct("!bbbbi")
_FORMAT_JSON = 1
_MAX_FRAME = 4 * 1024 * 1024


class ProtectError(Exception):
    """Base error."""


class CannotConnect(ProtectError):
    """The console could not be reached or returned an unexpected error."""


class InvalidAuth(ProtectError):
    """The console rejected the username or password."""


def _format_host(host: str, port: int) -> str:
    host = host.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 literal
    return host if port == 443 else f"{host}:{port}"


def decode_ws_message(payload: bytes) -> tuple[dict[str, Any], Any] | None:
    """Decode a Protect binary WebSocket message into (action, data).

    Returns None for messages that are not two JSON frames (e.g. binary
    buffers) instead of raising.
    """
    frames: list[Any] = []
    offset = 0
    for _ in range(2):
        if len(payload) < offset + _WS_HEADER.size:
            return None
        _ptype, fmt, deflated, _unk, size = _WS_HEADER.unpack_from(payload, offset)
        start = offset + _WS_HEADER.size
        end = start + size
        if size < 0 or size > _MAX_FRAME or end > len(payload):
            return None
        raw = payload[start:end]
        if deflated:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                return None
        if fmt != _FORMAT_JSON:
            frames.append(None)
        else:
            try:
                frames.append(json.loads(raw))
            except ValueError:
                return None
        offset = end
    action, data = frames
    if not isinstance(action, dict):
        return None
    return action, data


class ProtectClient:
    """Authenticated session against one UniFi OS console."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool,
    ) -> None:
        self._base = f"https://{_format_host(host, port)}"
        self._ws_base = f"wss://{_format_host(host, port)}"
        self._username = username
        self._password = password
        self._ssl: bool = verify_ssl
        self._session: aiohttp.ClientSession | None = None
        self._login_lock = asyncio.Lock()
        self._logged_in = False

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # A private session (and cookie jar) so this login never mixes with
            # other integrations; unsafe=True keeps cookies for IP-address hosts.
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True), timeout=_TIMEOUT
            )
            self._logged_in = False
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._logged_in = False

    async def login(self) -> None:
        async with self._login_lock:
            session = self._get_session()
            session.cookie_jar.clear()
            try:
                async with session.post(
                    f"{self._base}{_LOGIN_PATH}",
                    json={
                        "username": self._username,
                        "password": self._password,
                        "token": "",
                        "rememberMe": True,
                    },
                    ssl=self._ssl,
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (400, 401, 403):
                        raise InvalidAuth(f"Login rejected (HTTP {resp.status})")
                    if resp.status == 429:
                        raise CannotConnect("Login rate limited by the console (HTTP 429)")
                    if resp.status != 200:
                        raise CannotConnect(f"Login failed (HTTP {resp.status})")
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise CannotConnect(f"Cannot reach console: {err}") from err
            self._logged_in = True

    async def _get_json(self, path: str, params: Any = None) -> Any:
        if not self._logged_in:
            await self.login()
        for attempt in (1, 2):
            session = self._get_session()
            try:
                async with session.get(
                    f"{self._base}{path}",
                    params=params,
                    ssl=self._ssl,
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (401, 403) and attempt == 1:
                        self._logged_in = False
                        await self.login()
                        continue
                    if resp.status in (401, 403):
                        raise InvalidAuth(f"Access denied to {path} (HTTP {resp.status})")
                    if resp.status != 200:
                        raise CannotConnect(f"GET {path} failed (HTTP {resp.status})")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise CannotConnect(f"GET {path} failed: {err}") from err
        raise CannotConnect(f"GET {path} failed")

    async def get_bootstrap(self) -> dict[str, Any]:
        data = await self._get_json(_BOOTSTRAP_PATH)
        if not isinstance(data, dict) or not isinstance(data.get("nvr"), dict):
            raise CannotConnect("Unexpected bootstrap payload")
        return data

    async def get_sensors(self) -> list[dict[str, Any]]:
        data = await self._get_json(_SENSORS_PATH)
        if not isinstance(data, list):
            raise CannotConnect("Unexpected sensors payload")
        return [s for s in data if isinstance(s, dict)]

    async def get_events(
        self, types: list[str], start_ms: int, end_ms: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("start", str(start_ms)),
            ("end", str(end_ms)),
            ("limit", str(limit)),
            ("orderDirection", "DESC"),
        ]
        params.extend(("types", t) for t in types)
        data = await self._get_json(_EVENTS_PATH, params)
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict)]

    async def stream_updates(
        self, last_update_id: str | None
    ) -> AsyncIterator[tuple[dict[str, Any], Any]]:
        """Yield decoded (action, data) pairs until the socket closes."""
        if not self._logged_in:
            await self.login()
        url = f"{self._ws_base}{_WS_PATH}"
        params = {"lastUpdateId": last_update_id} if last_update_id else None
        session = self._get_session()
        try:
            async with session.ws_connect(
                url,
                params=params,
                ssl=self._ssl,
                heartbeat=30,
                max_msg_size=_MAX_FRAME * 2,
            ) as ws:
                _LOGGER.debug("Protect update stream connected")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        decoded = decode_ws_message(msg.data)
                        if decoded is not None:
                            yield decoded
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                    ):
                        break
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                self._logged_in = False
            raise CannotConnect(f"Update stream refused (HTTP {err.status})") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(f"Update stream failed: {err}") from err


def now_ms() -> int:
    return int(time.time() * 1000)
