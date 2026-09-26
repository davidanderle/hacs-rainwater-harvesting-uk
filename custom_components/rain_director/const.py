"""Constants for the Rain Director integration.

Protocol-level constants (checksum seed, flag bit tables, state codes)
live in :mod:`protocol`. This module holds only the Home Assistant
integration constants.
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "rain_director"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

DEFAULT_PORT = 8899

MANUFACTURER = "Rain Director"
MODEL = "RS485 bus (reverse engineered, via EW11A)"
