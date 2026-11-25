"""End-to-end tests against a faked Protect console."""

from __future__ import annotations

from datetime import timedelta
import struct
import zlib
import json

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.unifi_protect_air_smoke.api import InvalidAuth, decode_ws_message
from custom_components.unifi_protect_air_smoke.const import DOMAIN

from .conftest import now_ms

DATA = {"host": "192.0.2.10", "port": 443, "username": "ha", "password": "pw", "verify_ssl": False}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="aa:bb:cc:dd:ee:ff")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass, unique_id: str, domain: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def test_entities_created(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    temp = hass.states.get(_eid(hass, "aq1_temperature", "sensor"))
    assert temp.state == "21.5"
    assert temp.attributes["status"] == "good"
    assert hass.states.get(_eid(hass, "aq1_co2", "sensor")).state == "650.0"
    assert hass.states.get(_eid(hass, "aq1_pm25", "sensor")).state == "3.2"
    # Metrics absent from the payload are not created.
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "aq1_pm10") is None
    assert hass.states.get(_eid(hass, "aq1_air_quality_alert", "binary_sensor")).state == "off"
    assert hass.states.get(_eid(hass, "sm1_smoke", "binary_sensor")).state == "off"
    assert hass.states.get(_eid(hass, "sm1_co", "binary_sensor")).state == "off"
    _eid(hass, "sm1_alarm_event", "event")
    _eid(hass, "sm1_maintenance_event", "event")
    # Motion sensor ignored.
    assert not [e for e in er.async_get(hass).entities.values() if e.unique_id.startswith("mo1")]
    # Device merges by MAC connection.
    devs = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    smoke_dev = next(d for d in devs if (DOMAIN, "sm1") in d.identifiers)
    assert smoke_dev.name == "USL Smoke Kitchen"
    assert ("mac", "90:41:b2:e8:2e:ff") in smoke_dev.connections


async def test_smoke_alarm_push_lifecycle(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    smoke = _eid(hass, "sm1_smoke", "binary_sensor")
    co = _eid(hass, "sm1_co", "binary_sensor")
    alarm_event = _eid(hass, "sm1_alarm_event", "event")

    start = now_ms()
    coordinator._handle_push(
        {"action": "add", "modelKey": "event", "id": "ev1", "newUpdateId": "u2"},
        {"id": "ev1", "type": "sensorAlarm", "start": start, "end": None,
         "metadata": {"sensorId": {"text": "sm1"}, "alarmType": {"text": "smoke"}}},
    )
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "on"
    assert hass.states.get(co).state == "off"
    assert hass.states.get(alarm_event).attributes["event_type"] == "smoke"

    # A poll that does not yet know the event keeps the push-only alarm on.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "on"

    # Update with only the end time clears it immediately.
    coordinator._handle_push(
        {"action": "update", "modelKey": "event", "id": "ev1"}, {"end": start + 5000}
    )
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "off"


async def test_co_alarm_from_rest_poll(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    co = _eid(hass, "sm1_co", "binary_sensor")
    alarm_event = _eid(hass, "sm1_alarm_event", "event")
    start = now_ms()
    fake_api["events"] = [
        {"id": "ev2", "type": "sensorAlarm", "start": start, "end": None,
         "device": "sm1", "metadata": {"alarmType": {"text": "CO"}}}
    ]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(co).state == "on"
    assert hass.states.get(alarm_event).attributes["event_type"] == "co"

    fake_api["events"][0]["end"] = start + 1000
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(co).state == "off"


async def test_alarm_open_at_startup_without_event_replay(hass: HomeAssistant, fake_api) -> None:
    fake_api["events"] = [
        {"id": "old", "type": "sensorAlarm", "start": now_ms() - 3_600_000, "end": None,
         "device": "sm1", "metadata": {"alarmType": {"text": "smoke"}}}
    ]
    await _setup(hass)
    assert hass.states.get(_eid(hass, "sm1_smoke", "binary_sensor")).state == "on"
    assert hass.states.get(_eid(hass, "sm1_alarm_event", "event")).state == "unknown"


async def test_instant_event_pulses(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    smoke = _eid(hass, "sm1_smoke", "binary_sensor")
    start = now_ms()
    coordinator._handle_push(
        {"action": "add", "modelKey": "event", "id": "ev3"},
        {"id": "ev3", "type": "sensorAlarm", "start": start, "end": start,
         "device": "sm1", "metadata": {"alarmType": {"text": "smoke"}}},
    )
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "on"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert hass.states.get(smoke).state == "off"


async def test_maintenance_event(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    maint = _eid(hass, "sm1_maintenance_event", "event")
    coordinator._handle_push(
        {"action": "add", "modelKey": "event", "id": "ev4"},
        {"id": "ev4", "type": "sensorSmokeTest", "start": now_ms(), "end": None,
         "device": "sm1", "metadata": {"source": "local"}},
    )
    await hass.async_block_till_done()
    state = hass.states.get(maint)
    assert state.attributes["event_type"] == "self_test"
    assert state.attributes["source"] == "local"


async def test_air_push_update_and_alert(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator._handle_push(
        {"action": "update", "modelKey": "sensor", "id": "aq1"},
        {"airQuality": {"co2": {"value": 1850, "status": "high"}}},
    )
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "aq1_co2", "sensor")).state == "1850.0"
    alert = hass.states.get(_eid(hass, "aq1_air_quality_alert", "binary_sensor"))
    assert alert.state == "on"
    assert alert.attributes["metrics"] == ["co2"]
    # Other readings kept after the partial merge.
    assert hass.states.get(_eid(hass, "aq1_temperature", "sensor")).state == "21.5"

    coordinator._handle_push(
        {"action": "add", "modelKey": "event", "id": "ev5"},
        {"id": "ev5", "type": "sensorVape", "start": now_ms(), "end": None, "device": "aq1"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "aq1_vape", "binary_sensor")).state == "on"
    assert hass.states.get(_eid(hass, "aq1_air_alert_event", "event")).attributes["event_type"] == "vape"


async def test_new_metric_appears(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    entry.runtime_data._handle_push(
        {"action": "update", "modelKey": "sensor", "id": "aq1"},
        {"airQuality": {"pm10p0": {"value": 7, "status": "good"}}},
    )
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "aq1_pm10", "sensor")).state == "7.0"


async def test_disconnected_device_unavailable(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    fake_api["sensors"][1]["isConnected"] = False
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, "sm1_smoke", "binary_sensor")).state == "unavailable"


async def test_unload(hass: HomeAssistant, fake_api) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow(hass: HomeAssistant, fake_api) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UNVR-1"
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"


async def test_config_flow_invalid_auth(hass: HomeAssistant, fake_api) -> None:
    from unittest.mock import patch

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(
        "custom_components.unifi_protect_air_smoke.api.ProtectClient.login",
        side_effect=InvalidAuth("no"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}


def _frame(obj, deflate=False, fmt=1) -> bytes:
    raw = json.dumps(obj).encode()
    if deflate:
        raw = zlib.compress(raw)
    return struct.pack("!bbbbi", 1, fmt, 1 if deflate else 0, 0, len(raw)) + raw


def test_decode_ws_message() -> None:
    msg = _frame({"action": "update", "id": "x"}) + _frame({"a": 1}, deflate=True)
    assert decode_ws_message(msg) == ({"action": "update", "id": "x"}, {"a": 1})
    assert decode_ws_message(b"\x01\x02") is None
    binary = _frame({"action": "update"}) + struct.pack("!bbbbi", 2, 3, 0, 0, 2) + b"\x00\x01"
    assert decode_ws_message(binary) == ({"action": "update"}, None)
