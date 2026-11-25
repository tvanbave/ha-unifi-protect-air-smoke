"""Shared entity base and dynamic entity creation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ProtectConfigEntry, ProtectCoordinator


def device_info(raw: dict[str, Any]) -> DeviceInfo:
    """Device info that merges into the core UniFi Protect device by MAC."""
    info = DeviceInfo(
        identifiers={(DOMAIN, raw["id"])},
        manufacturer=MANUFACTURER,
        model=raw.get("marketName") or raw.get("type"),
        model_id=raw.get("type"),
        sw_version=raw.get("firmwareVersion"),
    )
    if isinstance(raw.get("name"), str) and raw["name"]:
        info["name"] = raw["name"]
    mac = raw.get("mac")
    if isinstance(mac, str) and len(mac.replace(":", "")) == 12:
        info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(mac))}
    return info


class ProtectSensorEntity(CoordinatorEntity[ProtectCoordinator]):
    """Entity bound to one Protect sensor device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ProtectCoordinator,
        sensor_id: str,
        description: EntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.sensor_id = sensor_id
        self._attr_unique_id = f"{sensor_id}_{description.key}"
        self._attr_device_info = device_info(coordinator.devices[sensor_id])

    @property
    def raw(self) -> dict[str, Any]:
        return self.coordinator.devices.get(self.sensor_id, {})

    @property
    def available(self) -> bool:
        raw = self.coordinator.devices.get(self.sensor_id)
        if raw is None:
            return False
        if raw.get("isConnected") is False:
            return False
        return self.coordinator.last_update_success or self.coordinator.stream_connected


def async_add_dynamic(
    entry: ProtectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[ProtectCoordinator, str, str, dict[str, Any]], Iterable[Entity]],
) -> None:
    """Add entities now and whenever a new device or metric appears."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _check() -> None:
        new: list[Entity] = []
        for sensor_id, raw in list(coordinator.devices.items()):
            kind = coordinator.kinds.get(sensor_id)
            if kind is None:
                continue
            for entity in factory(coordinator, sensor_id, kind, raw):
                if entity.unique_id and entity.unique_id not in known:
                    known.add(entity.unique_id)
                    coordinator.created_unique_ids.add(entity.unique_id)
                    new.append(entity)
        if new:
            async_add_entities(new)

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
