"""Constants for UniFi Protect Air & Smoke."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "unifi_protect_air_smoke"
MANUFACTURER = "Ubiquiti"

CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = False

# REST poll that reconciles readings and alarm state even if the push channel drops.
POLL_INTERVAL = timedelta(seconds=60)
# How far back the REST poll looks for smoke/CO alarm events that are still open.
ALARM_LOOKBACK = timedelta(hours=12)
# An alarm event that arrives already ended keeps the binary sensor on this long.
ENDED_ALARM_HOLD_SECONDS = 60

KIND_AIR = "air_quality"
KIND_SMOKE = "smoke"

# Event types (private Protect API) handled by this integration.
EVENT_SENSOR_ALARM = "sensorAlarm"
EVENT_EXTREME_VALUES = "sensorExtremeValues"
EVENT_VAPE = "sensorVape"
MAINTENANCE_EVENTS: dict[str, str] = {
    "sensorSmokeTest": "self_test",
    "sensorSmokeBatteryLow": "battery_low",
    "sensorSmokeNeedsCleaning": "needs_cleaning",
    "sensorSmokeFault": "smoke_fault",
    "sensorCoFault": "co_fault",
    "sensorSmokeEndOfLife": "end_of_life",
}
TRACKED_EVENT_TYPES = frozenset(
    {EVENT_SENSOR_ALARM, EVENT_EXTREME_VALUES, EVENT_VAPE, *MAINTENANCE_EVENTS}
)

# metadata.alarmType.text of a sensorAlarm event -> our alarm key.
ALARM_TYPES: dict[str, str] = {"smoke": "smoke", "co": "co"}

# Air-quality metric statuses that mean "not alarming".
OK_STATUSES = frozenset(
    {"good", "normal", "neutral", "safe", "ok", "none", "unknown", ""}
)

SIGNAL_EVENT = f"{DOMAIN}_event_{{entry_id}}"
