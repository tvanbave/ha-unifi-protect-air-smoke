"""UP-AirQuality measurement sensors."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import KIND_AIR
from .coordinator import ProtectConfigEntry, ProtectCoordinator
from .entity import ProtectSensorEntity, async_add_dynamic


@dataclass(frozen=True, kw_only=True)
class AirSensorDescription(SensorEntityDescription):
    """A metric and the payload keys it may appear under."""

    payload_keys: tuple[str, ...]


METRICS: tuple[AirSensorDescription, ...] = (
    AirSensorDescription(
        key="temperature",
        payload_keys=("temperature",),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    AirSensorDescription(
        key="humidity",
        payload_keys=("humidity",),
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AirSensorDescription(
        key="co2",
        payload_keys=("co2",),
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    AirSensorDescription(
        key="pm1",
        payload_keys=("pm1p0", "pm1"),
        device_class=SensorDeviceClass.PM1,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="pm25",
        payload_keys=("pm2p5", "pm2_5", "pm25"),
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="pm4",
        payload_keys=("pm4p0", "pm4"),
        device_class=SensorDeviceClass.PM4,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="pm10",
        payload_keys=("pm10p0", "pm10"),
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="tvoc",
        translation_key="tvoc",
        payload_keys=("tvoc",),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="voc_index",
        translation_key="voc_index",
        payload_keys=("voc", "vocIndex"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="nox_index",
        translation_key="nox_index",
        payload_keys=("nox", "noxIndex"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="aqi",
        payload_keys=("aqi",),
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    AirSensorDescription(
        key="vape_index",
        translation_key="vape_index",
        payload_keys=("vape", "vapeIndex"),
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


def read_metric(
    raw: Mapping[str, Any], keys: tuple[str, ...]
) -> tuple[bool, float | None, str | None]:
    """Return (present, value, status) for a metric, tolerating payload variants."""
    sources = [raw.get("airQuality"), raw.get("stats")]
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in keys:
            if key not in source:
                continue
            reading = source[key]
            status = None
            if isinstance(reading, Mapping):
                status = reading.get("status")
                reading = reading.get("value")
            value: float | None = None
            if isinstance(reading, (int, float)) and not isinstance(reading, bool):
                if math.isfinite(reading):
                    value = float(reading)
            return True, value, status if isinstance(status, str) else None
    return False, None, None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    def factory(
        coordinator: ProtectCoordinator, sensor_id: str, kind: str, raw: dict[str, Any]
    ) -> Iterable[AirQualitySensor]:
        if kind != KIND_AIR:
            return []
        return [
            AirQualitySensor(coordinator, sensor_id, desc)
            for desc in METRICS
            if read_metric(raw, desc.payload_keys)[0]
        ]

    async_add_dynamic(entry, async_add_entities, factory)


class AirQualitySensor(ProtectSensorEntity, SensorEntity):
    """One UP-AirQuality measurement."""

    entity_description: AirSensorDescription

    @property
    def native_value(self) -> float | None:
        return read_metric(self.raw, self.entity_description.payload_keys)[1]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = read_metric(self.raw, self.entity_description.payload_keys)[2]
        return {"status": status} if status else None
