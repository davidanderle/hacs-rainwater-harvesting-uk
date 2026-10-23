"""Config flow for Rain Director."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, UnitOfVolume
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_TANK_LITRES,
    DEFAULT_PORT,
    DEFAULT_TANK_LITRES,
    DOMAIN,
    MAX_TANK_LITRES,
    MIN_TANK_LITRES,
)

_LOGGER = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10
# The bus is very chatty (~10ms/message), so if this is really the
# EW11A's data port, at least one line should arrive almost instantly.
_TRAFFIC_TIMEOUT = 3


def _tank_selector() -> NumberSelector:
    """Number box for the loft tank volume, in litres."""
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_TANK_LITRES,
            max=MAX_TANK_LITRES,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=UnitOfVolume.LITERS,
        )
    )


class RainDirectorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rain Director."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle the (only) manual setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            tank_litres = float(user_input[CONF_TANK_LITRES])

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            errors = await self._async_test_connection(host, port)
            if not errors:
                return self.async_create_entry(
                    title=f"Rain Director ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_TANK_LITRES: tank_litres},
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_TANK_LITRES, default=DEFAULT_TANK_LITRES): _tank_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Update host/port and/or tank volume on an existing entry.

        Deliberately doesn't touch the entry's unique_id: pointing the
        same entry at a new device's IP is exactly what this step is
        for, so its entities, history, water totals and any dashboard or
        automation references to them are kept rather than starting over.

        The bridge is only test-connected if the host or port actually
        changed, so the tank volume can still be edited while the bridge
        is down (e.g. a dead EW11A awaiting its replacement).
        """
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            tank_litres = float(user_input[CONF_TANK_LITRES])

            connection_changed = (host, port) != (
                reconfigure_entry.data.get(CONF_HOST),
                reconfigure_entry.data.get(CONF_PORT),
            )
            if connection_changed:
                errors = await self._async_test_connection(host, port)
            if not errors:
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TANK_LITRES: tank_litres,
                    },
                )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=reconfigure_entry.data.get(CONF_HOST, "")
                ): str,
                vol.Required(
                    CONF_PORT, default=reconfigure_entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): int,
                vol.Required(
                    CONF_TANK_LITRES,
                    default=reconfigure_entry.data.get(CONF_TANK_LITRES, DEFAULT_TANK_LITRES),
                ): _tank_selector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=data_schema, errors=errors
        )

    @staticmethod
    async def _async_test_connection(host: str, port: int) -> dict[str, str]:
        """Open a TCP connection and wait briefly for at least one line.

        This proves there's a live, chatty serial bridge on the other
        end rather than just some unrelated open port answering the TCP
        handshake.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=_CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            return {"base": "cannot_connect"}

        try:
            await asyncio.wait_for(reader.read(256), timeout=_TRAFFIC_TIMEOUT)
        except asyncio.TimeoutError:
            return {"base": "no_traffic"}
        finally:
            writer.close()

        return {}
