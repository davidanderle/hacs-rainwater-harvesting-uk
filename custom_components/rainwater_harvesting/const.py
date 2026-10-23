"""Constants for the RainWater Harvesting integration.

Protocol-level constants (checksum seed, flag bit tables, state codes)
live in :mod:`protocol`. This module holds only the Home Assistant
integration constants.
"""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "rainwater_harvesting"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

DEFAULT_PORT = 8899

# Usable volume of the loft header tank between 0% and 100% on the level
# sensor. Turns level changes into litres for the water counters. Entries
# created before this option existed simply fall back to the default.
CONF_TANK_LITRES = "tank_litres"
DEFAULT_TANK_LITRES = 100
MIN_TANK_LITRES = 1
MAX_TANK_LITRES = 5000

MANUFACTURER = "RainWater Harvesting"
MODEL = "Rain Director"
