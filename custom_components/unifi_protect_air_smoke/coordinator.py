"""Coordinator: REST polling plus the Protect push stream."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, InvalidAuth, ProtectClient, now_ms
from .const import (
    ALARM_LOOKBACK,
    ALARM_TYPES,
    DOMAIN,
    ENDED_ALARM_HOLD_SECONDS,
    EVENT_EXTREME_VALUES,
    EVENT_SENSOR_ALARM,
    EVENT_VAPE,
    KIND_AIR,
    KIND_SMOKE,
    MAINTENANCE_EVENTS,
    POLL_INTERVAL,
    SIGNAL_EVENT,
    TRACKED_EVENT_TYPES,
)

_LOGGER = logging.getLogger(__name__)

# Only events that started this recently fire event entities, so a restart
# does not replay old alarms.
_FRESH_EVENT_MS = 5 * 60 * 1000
# A push-only active alarm survives a REST poll that has not caught up yet.
_PUSH_GRACE_MS = 2 * 60 * 1000


def device_kind(raw: Mapping[str, Any]) -> str | None:
    """Classify a Protect sensor payload as air-quality, smoke, or neither.

    Every Protect sensor carries an ``airQuality`` block (all null on models
    without the hardware), so the model type decides first.
    """
    text = " ".join(
        str(raw.get(k, "")) for k in ("type", "marketName", "model", "sku")
    ).upper()
    compact = "".join(c for c in text if c.isalnum())
    if "AIRQUALITY" in compact:
        return KIND_AIR
    flags = raw.get("featureFlags")
    flags = flags if isinstance(flags, Mapping) else {}
    alarm_types = flags.get("alarmTypes")
    if (
        "SMOKE" in compact
        or flags.get("smoke")
        or (isinstance(alarm_types, list) and "smoke" in alarm_types)
    ):
        return KIND_SMOKE
    air = raw.get("airQuality")
    if isinstance(air, Mapping) and any(
        isinstance(v, Mapping) and v.get("value") is not None for v in air.values()
    ):
        return KIND_AIR
    return None


def _text(meta: Mapping[str, Any], key: str) -> Any:
    value = meta.get(key)
    return value.get("text") if isinstance(value, Mapping) else value


def event_sensor_id(event: Mapping[str, Any]) -> str | None:
    """Find the sensor an event belongs to across firmware payload variants."""
    for key in ("device", "sensor", "sensorId"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    meta = event.get("metadata")
    if isinstance(meta, Mapping):
        for key in ("sensorId", "deviceId"):
            value = _text(meta, key)
            if isinstance(value, str) and value:
                return value
    return None


def event_alarm_key(event: Mapping[str, Any]) -> str | None:
    """Return 'smoke' / 'co' for a sensorAlarm event, else None."""
    meta = event.get("metadata")
    if not isinstance(meta, Mapping):
        return None
    alarm = _text(meta, "alarmType")
    if not isinstance(alarm, str):
        return None
    return ALARM_TYPES.get(alarm.casefold())


def _deep_merge(target: dict[str, Any], update: Mapping[str, Any]) -> None:
    for key, value in update.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = deepcopy(value)


@dataclass
class ActiveEvent:
    """An open (not yet ended) alarm-like event."""

    sensor_id: str
    key: str  # smoke / co / vape / extreme
    start: int
    via_push: bool
    detail: dict[str, Any]


type ProtectConfigEntry = ConfigEntry[ProtectCoordinator]


class ProtectCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Keep sensor payloads and alarm state for one Protect console."""

    config_entry: ProtectConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ProtectConfigEntry, client: ProtectClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
        )
        self.client = client
        self.devices: dict[str, dict[str, Any]] = {}
        self.kinds: dict[str, str] = {}
        self.nvr: dict[str, Any] = {}
        self.active: dict[str, ActiveEvent] = {}
        self.pulses: dict[tuple[str, str], float] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=50)
        self.stream_connected = False
        self.created_unique_ids: set[str] = set()
        self._events: dict[str, dict[str, Any]] = {}
        self._seen: set[str] = set()
        self._primed = False
        self._last_update_id: str | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._timers: set[Any] = set()

    # ----------------------------------------------------------------- setup

    async def async_prime(self) -> None:
        """Read the bootstrap once (NVR identity, devices, stream cursor)."""
        try:
            bootstrap = await self.client.get_bootstrap()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        self.nvr = bootstrap.get("nvr", {})
        self._last_update_id = bootstrap.get("lastUpdateId")
        self._replace_devices(bootstrap.get("sensors") or [])

    def start_stream(self) -> None:
        self._ws_task = self.config_entry.async_create_background_task(
            self.hass, self._stream_loop(), f"{DOMAIN} update stream"
        )

    async def async_shutdown(self) -> None:
        for cancel in list(self._timers):
            cancel()
        self._timers.clear()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._ws_task = None
        await super().async_shutdown()
        await self.client.close()

    # ------------------------------------------------------------ REST poll

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            sensors = await self.client.get_sensors()
            end = now_ms()
            start = end - int(ALARM_LOOKBACK.total_seconds() * 1000)
            events = await self.client.get_events(
                sorted(TRACKED_EVENT_TYPES), start, end
            )
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnect as err:
            raise UpdateFailed(str(err)) from err

        self._replace_devices(sensors)
        self._reconcile_events(events, end)
        self._primed = True
        return self.devices

    def _replace_devices(self, sensors: list[Any]) -> None:
        devices: dict[str, dict[str, Any]] = {}
        kinds: dict[str, str] = {}
        for raw in sensors:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            kind = device_kind(raw)
            if kind is None:
                continue
            devices[raw["id"]] = raw
            kinds[raw["id"]] = kind
        self.devices = devices
        self.kinds = kinds

    def _reconcile_events(self, events: list[dict[str, Any]], now: int) -> None:
        open_ids: set[str] = set()
        for event in sorted(events, key=lambda e: e.get("start") or 0):
            event_id = event.get("id")
            if not isinstance(event_id, str):
                continue
            self._events[event_id] = event
            if self._handle_event(event_id, event, from_push=False):
                open_ids.add(event_id)
        # REST is authoritative, except for very recent push-only alarms.
        for event_id, active in list(self.active.items()):
            if event_id in open_ids:
                continue
            if active.via_push and now - active.start < _PUSH_GRACE_MS:
                continue
            self.active.pop(event_id, None)
        if len(self._events) > 500:
            for event_id in list(self._events)[:-250]:
                self._events.pop(event_id, None)
                self._seen.discard(event_id)

    # ---------------------------------------------------------- push stream

    async def _stream_loop(self) -> None:
        backoff = 5
        while True:
            try:
                if self._last_update_id is None:
                    bootstrap = await self.client.get_bootstrap()
                    self._last_update_id = bootstrap.get("lastUpdateId")
                async for action, data in self.client.stream_updates(
                    self._last_update_id
                ):
                    if not self.stream_connected:
                        self.stream_connected = True
                        backoff = 5
                    self._handle_push(action, data)
            except asyncio.CancelledError:
                raise
            except InvalidAuth:
                _LOGGER.warning("Protect rejected the credentials; reauthentication needed")
                self.stream_connected = False
                self.config_entry.async_start_reauth(self.hass)
                return
            except CannotConnect as err:
                _LOGGER.debug("Protect update stream dropped: %s", err)
            except Exception:  # noqa: BLE001 - never let the stream task die
                _LOGGER.exception("Unexpected error in Protect update stream")
            self.stream_connected = False
            # Fetch a fresh cursor on reconnect; the REST poll covers the gap.
            self._last_update_id = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    @callback
    def _handle_push(self, action: dict[str, Any], data: Any) -> None:
        update_id = action.get("newUpdateId")
        if isinstance(update_id, str):
            self._last_update_id = update_id
        model = action.get("modelKey")
        op = action.get("action")
        item_id = action.get("id")
        if not isinstance(item_id, str) or not isinstance(data, Mapping):
            return

        if model == "sensor":
            if op == "update" and item_id in self.devices:
                _deep_merge(self.devices[item_id], data)
                self.async_set_updated_data(self.devices)
            elif op == "add" and isinstance(data.get("id"), str):
                kind = device_kind(data)
                if kind is not None:
                    self.devices[item_id] = deepcopy(dict(data))
                    self.kinds[item_id] = kind
                    self.async_set_updated_data(self.devices)
            elif op == "remove" and item_id in self.devices:
                self.devices.pop(item_id)
                self.kinds.pop(item_id, None)
                self.async_set_updated_data(self.devices)
            return

        if model == "event" and op in ("add", "update"):
            merged = self._events.setdefault(item_id, {"id": item_id})
            _deep_merge(merged, data)
            if merged.get("type") not in TRACKED_EVENT_TYPES:
                if item_id not in self._seen:
                    self._events.pop(item_id, None)
                return
            self._handle_event(item_id, merged, from_push=True)
            self.async_update_listeners()

    # ------------------------------------------------------ event semantics

    def _handle_event(
        self, event_id: str, event: Mapping[str, Any], *, from_push: bool
    ) -> bool:
        """Apply one event; return True when it is a still-open alarm."""
        event_type = event.get("type")
        sensor_id = event_sensor_id(event)
        if sensor_id is None or sensor_id not in self.kinds:
            return False
        start = event.get("start") if isinstance(event.get("start"), int) else now_ms()
        ended = event.get("end") is not None
        first_seen = event_id not in self._seen
        self._seen.add(event_id)
        fresh = self._primed and now_ms() - start < _FRESH_EVENT_MS
        meta = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}

        if first_seen:
            self.recent_events.append(deepcopy(dict(event)))

        key: str | None = None
        detail: dict[str, Any] = {}
        if event_type == EVENT_SENSOR_ALARM:
            key = event_alarm_key(event)
        elif event_type == EVENT_VAPE:
            key = "vape"
        elif event_type == EVENT_EXTREME_VALUES:
            key = "extreme"
            detail = {
                "metric": _text(meta, "sensorType"),
                "value": _text(meta, "sensorValue"),
                "status": _text(meta, "status"),
            }
        elif event_type in MAINTENANCE_EVENTS:
            if first_seen and fresh:
                self._fire(sensor_id, "maintenance", MAINTENANCE_EVENTS[event_type], {
                    "source": meta.get("source"),
                })
            return False
        if key is None:
            return False

        if first_seen and fresh:
            self._fire(sensor_id, "alarm", key, detail)
        if ended:
            self.active.pop(event_id, None)
            if first_seen and fresh:
                # Never seen open (instant event, or it opened and closed
                # between polls): hold it on briefly so it is not missed.
                self._pulse(sensor_id, key)
            return False
        self.active[event_id] = ActiveEvent(sensor_id, key, start, from_push, detail)
        return True

    def _fire(self, sensor_id: str, channel: str, event_type: str, attrs: dict) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_EVENT.format(entry_id=self.config_entry.entry_id),
            sensor_id,
            channel,
            event_type,
            {k: v for k, v in attrs.items() if v is not None},
        )

    def _pulse(self, sensor_id: str, key: str) -> None:
        """Hold an alarm on briefly when it arrived already ended."""
        pulse_key = (sensor_id, key)
        token = time.monotonic()
        self.pulses[pulse_key] = token

        @callback
        def _expire(_now: Any) -> None:
            self._timers.discard(cancel)
            if self.pulses.get(pulse_key) == token:
                self.pulses.pop(pulse_key, None)
                self.async_update_listeners()

        cancel = async_call_later(self.hass, ENDED_ALARM_HOLD_SECONDS, _expire)
        self._timers.add(cancel)

    # --------------------------------------------------------------- queries

    def alarm_active(self, sensor_id: str, key: str) -> bool:
        if any(a.sensor_id == sensor_id and a.key == key for a in self.active.values()):
            return True
        return (sensor_id, key) in self.pulses

    def active_details(self, sensor_id: str, key: str) -> list[dict[str, Any]]:
        return [
            a.detail
            for a in self.active.values()
            if a.sensor_id == sensor_id and a.key == key
        ]
