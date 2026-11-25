"""Smoke / CO alarm and air-quality alert binary sensors."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KIND_AIR, KIND_SMOKE, OK_STATUSES
from .coordinator import ProtectConfigEntry, ProtectCoordinator
from .entity import ProtectSensorEntity, async_add_dynamic
from .sensor import METRICS, read_metric

SMOKE = BinarySensorEntityDescription(
    key="smoke", device_class=BinarySensorDeviceClass.SMOKE
)
CO = BinarySensorEntityDescription(
    key="co", device_class=BinarySensorDeviceClass.CO
)
AIR_ALERT = BinarySensorEntityDescription(
    key="air_quality_alert",
    translation_key="air_quality_alert",
    device_class=BinarySensorDeviceClass.PROBLEM,
)
VAPE = BinarySensorEntityDescription(
    key="vape", translation_key="vape", device_class=BinarySensorDeviceClass.PROBLEM
)

# Boolean flags a smoke detector payload might carry directly.
_RAW_FLAGS = {
    "smoke": ("isSmokeDetected", "smokeDetected", "isSmokeAlarmActive"),
    "co": ("isCoDetected", "coDetected", "isCoAlarmActive"),
}


def _is_alarm_status(status: str | None) -> bool:
    return status is not None and status.casefold() not in OK_STATUSES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(
        coordinator: ProtectCoordinator, sensor_id: str, kind: str, raw: dict[str, Any]
    ) -> Iterable[BinarySensorEntity]:
        if kind == KIND_SMOKE:
            return [
                SmokeAlarmBinarySensor(coordinator, sensor_id, SMOKE),
                SmokeAlarmBinarySensor(coordinator, sensor_id, CO),
            ]
        if kind == KIND_AIR:
            entities: list[BinarySensorEntity] = [
                AirAlertBinarySensor(coordinator, sensor_id, AIR_ALERT)
            ]
            if read_metric(raw, ("vape", "vapeIndex"))[0]:
                entities.append(VapeBinarySensor(coordinator, sensor_id, VAPE))
            return entities
        return []

    async_add_dynamic(entry, async_add_entities, factory)


class SmokeAlarmBinarySensor(ProtectSensorEntity, BinarySensorEntity):
    """On while Protect reports an open smoke or CO alarm."""

    @property
    def is_on(self) -> bool:
        key = self.entity_description.key
        if self.coordinator.alarm_active(self.sensor_id, key):
            return True
        return any(self.raw.get(flag) is True for flag in _RAW_FLAGS[key])


class AirAlertBinarySensor(ProtectSensorEntity, BinarySensorEntity):
    """On when any air-quality metric is outside its threshold."""

    def _alerts(self) -> list[str]:
        alerts = [
            desc.key
            for desc in METRICS
            if _is_alarm_status(read_metric(self.raw, desc.payload_keys)[2])
        ]
        for detail in self.coordinator.active_details(self.sensor_id, "extreme"):
            metric = detail.get("metric")
            if isinstance(metric, str) and metric not in alerts:
                alerts.append(metric)
        return alerts

    @property
    def is_on(self) -> bool:
        return bool(self._alerts()) or self.coordinator.alarm_active(
            self.sensor_id, "extreme"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"metrics": self._alerts()}


class VapeBinarySensor(ProtectSensorEntity, BinarySensorEntity):
    """On while vaping is detected."""

    @property
    def is_on(self) -> bool:
        status = read_metric(self.raw, ("vape", "vapeIndex"))[2]
        return _is_alarm_status(status) or self.coordinator.alarm_active(
            self.sensor_id, "vape"
        )
