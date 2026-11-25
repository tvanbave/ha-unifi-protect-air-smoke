"""Diagnostics: redacted raw payloads, to map new firmware fields."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import ProtectConfigEntry

_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_HOST,
    "mac",
    "host",
    "ip",
    "ipAddress",
    "bluetoothConnectionState",
    "guid",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ProtectConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), _REDACT),
        "stream_connected": coordinator.stream_connected,
        "last_update_success": coordinator.last_update_success,
        "devices": {
            sensor_id: {
                "kind": coordinator.kinds.get(sensor_id),
                "payload": async_redact_data(raw, _REDACT),
            }
            for sensor_id, raw in coordinator.devices.items()
        },
        "active_alarms": {
            event_id: {
                "sensor_id": a.sensor_id,
                "key": a.key,
                "start": a.start,
                "via_push": a.via_push,
                "detail": a.detail,
            }
            for event_id, a in coordinator.active.items()
        },
        "recent_events": async_redact_data(list(coordinator.recent_events), _REDACT),
    }
