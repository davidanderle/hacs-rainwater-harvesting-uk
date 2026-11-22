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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .protocol import BusFrameDecoder, RainDirectorData, latched_values, restore_latched
from .water import WaterMeter, WaterTotals

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY_INITIAL = 5
_RECONNECT_DELAY_MAX = 60
_READ_CHUNK = 4096

# Water totals are written to .storage a little after they change rather
# than on every 1% step; HA also flushes pending writes on shutdown.
_STORAGE_VERSION = 1
_SAVE_DELAY = 30

# Latched values (commissioning data, last display/action) change rarely and
# are worth far more than a few seconds of write-avoidance: a hard power cut
# loses anything still pending, and those messages are never re-sent. So
# they are written almost immediately; the short delay just merges a burst.
_LATCH_SAVE_DELAY = 2

# The bridge has been observed to terminate frames with a bare '\r',
# which plain line iteration does not split on (only '\n' does) -- so
# split on either, same as the original sniffer script's iter_lines().
_LINE_SPLIT_RE = re.compile(r"[\r\n]")


class RainDirectorCoordinator(DataUpdateCoordinator[RainDirectorData]):
    """Owns the TCP connection to the EW11A and the decoded bus state."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        entry_id: str,
        tank_litres: float,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}_{host}", update_interval=None)
        self.host = host
        self.port = port
        self.data = RainDirectorData()
        self._decoder = BusFrameDecoder()
        self._task: asyncio.Task | None = None
        self._stopping = False

        # Running litre totals, kept in their own store (not entity state)
        # so they survive restarts AND integration reloads -- Reconfigure
        # reloads the entry, and the totals must carry straight through.
        self.meter = WaterMeter(tank_litres)
        self._store: Store[dict] = Store(hass, _STORAGE_VERSION, f"{DOMAIN}.{entry_id}.water")

        # Last known value of the rarely-sent messages (see LATCHED_FIELDS),
        # in a separate store from the water totals so neither format has to
        # change when the other does.
        self._latched_store: Store[dict] = Store(
            hass, _STORAGE_VERSION, f"{DOMAIN}.{entry_id}.latched"
        )

    @property
    def tank_litres(self) -> float:
        """Configured usable tank volume in litres."""
        return self.meter.tank_litres

    async def async_load_totals(self) -> None:
        """Restore the saved water totals. Call before async_start()."""
        self.meter.totals = WaterTotals.from_dict(await self._store.async_load())

    def _water_data(self) -> dict[str, float]:
        return self.meter.totals.to_dict()

    async def async_load_latched(self) -> None:
        """Put back the last known value of the rarely-sent messages.

        Call before async_start() and before the entities are created, so
        they have something to show from the very first moment.
        """
        self.data = restore_latched(self.data, await self._latched_store.async_load())

    def _latched_data(self) -> dict[str, str | None]:
        return latched_values(self.data)

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
        # Flush any pending delayed write so nothing is lost on unload/reload.
        await self._store.async_save(self._water_data())
        await self._latched_store.async_save(self._latched_data())

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

            self._decoder.reset_connection()

            buffer = ""
            synced = False
            while True:
                chunk = await reader.read(_READ_CHUNK)
                if not chunk:
                    raise OSError("EW11A closed the connection")
                buffer += chunk.decode("ascii", errors="ignore")

                if not synced:
                    # We may have joined mid-line; drop up to the first line break.
                    first_break = _LINE_SPLIT_RE.search(buffer)
                    if first_break is None:
                        continue
                    buffer = buffer[first_break.end():]
                    synced = True

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
            if self.meter.update(new_data.tank_level, new_data.led_flags, new_data.mode_code):
                self._store.async_delay_save(self._water_data, _SAVE_DELAY)
            if latched_values(new_data) != latched_values(self.data):
                self._latched_store.async_delay_save(self._latched_data, _LATCH_SAVE_DELAY)
            self.async_set_updated_data(new_data)

    def _set_connected(self, connected: bool) -> None:
        if not connected:
            # Whatever the level did while we weren't listening is unknown,
            # so the first reading after reconnecting is only a new baseline.
            self.meter.reset_baseline()
        if self.data.connected == connected:
            return
        self.async_set_updated_data(replace(self.data, connected=connected))
