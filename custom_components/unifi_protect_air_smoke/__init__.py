"""UniFi Protect Air & Smoke: UP-AirQuality readings and USL Smoke alarms."""

from __future__ import annotations

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import CannotConnect, InvalidAuth, ProtectClient
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL
from .coordinator import ProtectConfigEntry, ProtectCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ProtectConfigEntry) -> bool:
    client = ProtectClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    coordinator = ProtectCoordinator(hass, entry, client)
    try:
        await coordinator.async_prime()
    except InvalidAuth as err:
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except CannotConnect as err:
        await client.close()
        raise ConfigEntryNotReady(str(err)) from err
    except ConfigEntryAuthFailed:
        await client.close()
        raise

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await client.close()
        raise

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_stream()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProtectConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ProtectConfigEntry, device_entry
) -> bool:
    """Allow removing a device that Protect no longer reports."""
    coordinator = entry.runtime_data
    return not any(
        identifier[1] in coordinator.devices for identifier in device_entry.identifiers
    )
