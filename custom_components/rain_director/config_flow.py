"""Config flow for Rain Director."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10
# The bus is very chatty (~10ms/message), so if this is really the
# EW11A's data port, at least one line should arrive almost instantly.
_TRAFFIC_TIMEOUT = 3


class RainDirectorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rain Director."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle the (only) manual setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            errors = await self._async_test_connection(host, port)
            if not errors:
                return self.async_create_entry(
                    title=f"Rain Director ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

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
