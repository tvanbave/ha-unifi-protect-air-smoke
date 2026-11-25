"""Smoke / CO alarm, detector health and air-quality alert binary sensors."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KIND_AIR, KIND_SMOKE, OK_STATUSES
from .coordinator import ProtectConfigEntry, ProtectCoordinator
from .entity import ProtectSensorEntity, async_add_dynamic
from .sensor import METRICS, read_metric


def smoke_status(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    status = raw.get("smokeStatus")
    return status if isinstance(status, Mapping) else {}


@dataclass(frozen=True, kw_only=True)
class SmokeBinaryDescription(BinarySensorEntityDescription):
    """A USL Smoke flag, read from ``smokeStatus`` and/or alarm events."""

    status_key: str
    alarm_key: str | None = None  # also on while a matching alarm event is open
    extra: Callable[[Mapping[str, Any]], bool] | None = None


SMOKE_DESCRIPTIONS: tuple[SmokeBinaryDescription, ...] = (
    SmokeBinaryDescription(
        key="smoke",
        device_class=BinarySensorDeviceClass.SMOKE,
        status_key="smokeAlarm",
        alarm_key="smoke",
    ),
    SmokeBinaryDescription(
        key="co",
        device_class=BinarySensorDeviceClass.CO,
        status_key="coAlarm",
        alarm_key="co",
    ),
    SmokeBinaryDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="batteryLow",
        extra=lambda raw: (raw.get("batteryStatus") or {}).get("isLow") is True,
    ),
    SmokeBinaryDescription(
        key="needs_cleaning",
        translation_key="needs_cleaning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="cleanSmokeSensor",
    ),
    SmokeBinaryDescription(
        key="smoke_sensor_fault",
        translation_key="smoke_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="smokeSensorFault",
    ),
    SmokeBinaryDescription(
        key="co_sensor_fault",
        translation_key="co_sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="coSensorFault",
    ),
    SmokeBinaryDescription(
        key="end_of_life",
        translation_key="end_of_life",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="endOfLife",
    ),
    SmokeBinaryDescription(
        key="testing",
        translation_key="testing",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="testing",
        extra=lambda raw: raw.get("isTestModeActive") is True,
    ),
    SmokeBinaryDescription(
        key="alarm_silenced",
        translation_key="alarm_silenced",
        entity_category=EntityCategory.DIAGNOSTIC,
        status_key="alarmSilenced",
    ),
)

AIR_ALERT = BinarySensorEntityDescription(
    key="air_quality_alert",
    translation_key="air_quality_alert",
    device_class=BinarySensorDeviceClass.PROBLEM,
)
VAPE = BinarySensorEntityDescription(
    key="vape", translation_key="vape", device_class=BinarySensorDeviceClass.PROBLEM
)


def _is_alarm_status(status: str | None) -> bool:
    return status is not None and status.casefold() not in OK_STATUSES


def smoke_keys() -> set[str]:
    return {d.key for d in SMOKE_DESCRIPTIONS}


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
                SmokeBinarySensor(coordinator, sensor_id, desc)
                for desc in SMOKE_DESCRIPTIONS
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


class SmokeBinarySensor(ProtectSensorEntity, BinarySensorEntity):
    """A USL Smoke alarm or health flag."""

    entity_description: SmokeBinaryDescription

    @property
    def is_on(self) -> bool:
        desc = self.entity_description
        if smoke_status(self.raw).get(desc.status_key) is True:
            return True
        if desc.alarm_key and self.coordinator.alarm_active(self.sensor_id, desc.alarm_key):
            return True
        return bool(desc.extra and desc.extra(self.raw))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.alarm_key is None:
            return None
        status = smoke_status(self.raw)
        return {
            k: status[k]
            for k in ("ready", "enabled", "alarmSilenced")
            if k in status
        } or None


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
