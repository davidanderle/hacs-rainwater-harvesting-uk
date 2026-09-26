"""Data coordinator for the Rain Director RS485 bus.

The EW11A bridge exposes the bus as a raw TCP stream -- the same one
you'd get from `nc <host> <port>` -- pushing every message on the bus
the instant it happens. There's nothing to poll and no request/response
protocol to drive from a timer, so (like the BLE coordinator in this
author's other integration, which faces the same "advertisements just
arrive" shape) this coordinator sets `update_interval=None` and instead
keeps a persistent connection open in the background for as long as the
config entry is loaded, feeding every line through the protocol decoder
and pushing a new snapshot to Home Assistant only when something an
entity cares about actually changed.

Reconnection uses exponential backoff (5s, 10s, 20s, ... capped at 60s)
so a WiFi blip or an EW11A reboot doesn't spin the loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .protocol import BusFrameDecoder, RainDirectorData

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY_INITIAL = 5
_RECONNECT_DELAY_MAX = 60
_READ_CHUNK = 4096

# The bridge has been observed to terminate frames with a bare '\r',
# which plain line iteration does not split on (only '\n' does) -- so
# split on either, same as the original sniffer script's iter_lines().
_LINE_SPLIT_RE = re.compile(r"[\r\n]")


class RainDirectorCoordinator(DataUpdateCoordinator[RainDirectorData]):
    """Owns the TCP connection to the EW11A and the decoded bus state."""

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        """Initialize coordinator."""
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{host}", update_interval=None)
        self.host = host
        self.port = port
        self.data = RainDirectorData()
        self._decoder = BusFrameDecoder()
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def async_start(self) -> None:
        """Start the background connection task."""
        self._stopping = False
        self._task = self.hass.loop.create_task(self._run())

    async def async_stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _async_update_data(self) -> RainDirectorData:
        # update_interval=None means this is never called on a timer --
        # it only exists to satisfy DataUpdateCoordinator's interface.
        return self.data

    async def _run(self) -> None:
        """Connect, read until the connection drops, then retry with backoff."""
        delay = _RECONNECT_DELAY_INITIAL
        while not self._stopping:
            try:
                await self._connect_and_read()
                delay = _RECONNECT_DELAY_INITIAL  # clean session -- reset backoff
            except asyncio.CancelledError:
                raise
            except OSError as err:
                _LOGGER.warning(
                    "Connection to EW11A at %s:%s lost: %s", self.host, self.port, err
                )
            self._set_connected(False)
            if self._stopping:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX)

    async def _connect_and_read(self) -> None:
        _LOGGER.debug("Connecting to EW11A at %s:%s", self.host, self.port)
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            self._set_connected(True)
            _LOGGER.info("Connected to EW11A at %s:%s", self.host, self.port)

            buffer = ""
            while True:
                chunk = await reader.read(_READ_CHUNK)
                if not chunk:
                    raise OSError("EW11A closed the connection")
                buffer += chunk.decode("ascii", errors="ignore")

                *lines, buffer = _LINE_SPLIT_RE.split(buffer)
                for line in lines:
                    self._handle_line(line.strip())
        finally:
            writer.close()

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        new_data, notices = self._decoder.process_line(line, self.data)
        for notice in notices:
            _LOGGER.warning("%s", notice)
        if new_data is not None:
            self.async_set_updated_data(new_data)

    def _set_connected(self, connected: bool) -> None:
        if self.data.connected == connected:
            return
        self.async_set_updated_data(replace(self.data, connected=connected))
