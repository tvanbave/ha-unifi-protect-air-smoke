"""Config flow for UniFi Protect Air & Smoke."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.device_registry import format_mac

from .api import CannotConnect, InvalidAuth, ProtectClient
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _validate(data: Mapping[str, Any]) -> tuple[str, str]:
    """Log in and read the bootstrap; return (unique_id, title)."""
    client = ProtectClient(
        data[CONF_HOST],
        data.get(CONF_PORT, DEFAULT_PORT),
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    try:
        await client.login()
        bootstrap = await client.get_bootstrap()
    finally:
        await client.close()
    nvr = bootstrap["nvr"]
    mac = nvr.get("mac") or nvr.get("id") or data[CONF_HOST]
    unique_id = format_mac(mac) if isinstance(mac, str) and len(mac) >= 12 else str(mac)
    return unique_id, nvr.get("name") or data[CONF_HOST]


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


class ProtectAirSmokeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup, reauthentication and reconfiguration."""

    VERSION = 1

    async def _try(self, data: Mapping[str, Any], errors: dict[str, str]) -> tuple[str, str] | None:
        try:
            return await _validate(data)
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except CannotConnect as err:
            _LOGGER.debug("Cannot connect: %s", err)
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating the console")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._try(user_input, errors)
            if result is not None:
                unique_id, title = result
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            result = await self._try(data, errors)
            if result is not None:
                await self.async_set_unique_id(result[0])
                self._abort_if_unique_id_mismatch(reason="wrong_console")
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._try(user_input, errors)
            if result is not None:
                await self.async_set_unique_id(result[0])
                self._abort_if_unique_id_mismatch(reason="wrong_console")
                return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )
