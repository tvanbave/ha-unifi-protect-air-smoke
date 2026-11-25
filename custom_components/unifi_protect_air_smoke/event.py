"""Event entities for smoke/CO alarms, detector maintenance and air alerts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KIND_AIR, KIND_SMOKE, MAINTENANCE_EVENTS, SIGNAL_EVENT
from .coordinator import ProtectConfigEntry, ProtectCoordinator
from .entity import ProtectSensorEntity, async_add_dynamic

SMOKE_ALARM = EventEntityDescription(
    key="alarm_event", translation_key="alarm_event", event_types=["smoke", "co"]
)
MAINTENANCE = EventEntityDescription(
    key="maintenance_event",
    translation_key="maintenance_event",
    event_types=sorted(MAINTENANCE_EVENTS.values()),
)
AIR_EVENT = EventEntityDescription(
    key="air_alert_event",
    translation_key="air_alert_event",
    event_types=["extreme", "vape"],
)

# Which dispatcher channel and event types feed each entity.
_CHANNEL = {
    "alarm_event": ("alarm", {"smoke", "co"}),
    "maintenance_event": ("maintenance", set(MAINTENANCE_EVENTS.values())),
    "air_alert_event": ("alarm", {"extreme", "vape"}),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(
        coordinator: ProtectCoordinator, sensor_id: str, kind: str, raw: dict[str, Any]
    ) -> Iterable[ProtectEvent]:
        if kind == KIND_SMOKE:
            return [
                ProtectEvent(coordinator, sensor_id, SMOKE_ALARM),
                ProtectEvent(coordinator, sensor_id, MAINTENANCE),
            ]
        if kind == KIND_AIR:
            return [ProtectEvent(coordinator, sensor_id, AIR_EVENT)]
        return []

    async_add_dynamic(entry, async_add_entities, factory)


class ProtectEvent(ProtectSensorEntity, EventEntity):
    """Fires when Protect reports a new alarm or maintenance event."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_EVENT.format(entry_id=self.coordinator.config_entry.entry_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(
        self, sensor_id: str, channel: str, event_type: str, attrs: dict[str, Any]
    ) -> None:
        wanted_channel, types = _CHANNEL[self.entity_description.key]
        if sensor_id != self.sensor_id or channel != wanted_channel or event_type not in types:
            return
        self._trigger_event(event_type, attrs)
        self.async_write_ha_state()
