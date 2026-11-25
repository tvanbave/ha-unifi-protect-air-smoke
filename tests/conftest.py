"""Shared fixtures."""

from __future__ import annotations

import copy
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

AQ = {
    "id": "aq1",
    "type": "UP-AirQuality",
    "marketName": "UP Air Quality",
    "name": "UP Air Quality",
    "mac": "74F92CF820B8",
    "firmwareVersion": "1.0.19",
    "isConnected": True,
    "modelKey": "sensor",
    "airQuality": {
        "temperature": {"value": 21.5, "status": "good"},
        "humidity": {"value": 41, "status": "good"},
        "co2": {"value": 650, "status": "good"},
        "pm2p5": {"value": 3.2, "status": "good"},
        "tvoc": {"value": 120, "status": "good"},
        "voc": {"value": 100, "status": "good"},
        "vape": {"value": 0, "status": "good"},
        "futureMetric": {"value": "weird", "status": "unheard-of"},
    },
}
SMOKE = {
    "id": "sm1",
    "type": "USL-Smoke-US",
    "marketName": "USL Smoke",
    "name": "USL Smoke Kitchen",
    "mac": "9041B2E82EFF",
    "firmwareVersion": "1.4.0",
    "isConnected": True,
    "modelKey": "sensor",
}
MOTION = {"id": "mo1", "type": "USL-Motion-US", "marketName": "USL Motion", "mac": "7DB4FF804F03"}


def now_ms() -> int:
    return int(time.time() * 1000)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_api():
    """Patch the Protect client with in-memory state."""
    state: dict[str, Any] = {
        "sensors": [copy.deepcopy(AQ), copy.deepcopy(SMOKE), copy.deepcopy(MOTION)],
        "events": [],
    }

    async def bootstrap():
        return {
            "nvr": {"mac": "AABBCCDDEEFF", "name": "UNVR-1"},
            "lastUpdateId": "u1",
            "sensors": copy.deepcopy(state["sensors"]),
        }

    async def sensors():
        return copy.deepcopy(state["sensors"])

    async def events(types, start, end, limit=200):
        return copy.deepcopy(state["events"])

    with (
        patch("custom_components.unifi_protect_air_smoke.api.ProtectClient.login", AsyncMock()),
        patch("custom_components.unifi_protect_air_smoke.api.ProtectClient.get_bootstrap", side_effect=bootstrap),
        patch("custom_components.unifi_protect_air_smoke.api.ProtectClient.get_sensors", side_effect=sensors),
        patch("custom_components.unifi_protect_air_smoke.api.ProtectClient.get_events", side_effect=events),
        patch("custom_components.unifi_protect_air_smoke.coordinator.ProtectCoordinator.start_stream"),
    ):
        yield state
