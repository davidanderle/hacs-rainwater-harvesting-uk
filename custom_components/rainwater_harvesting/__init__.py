"""The Rain Director integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import RainDirectorCoordinator


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from YAML (not supported)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rain Director from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = RainDirectorCoordinator(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    await coordinator.async_start()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if not unload_ok:
        return False

    coordinator: RainDirectorCoordinator | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_stop()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return True
