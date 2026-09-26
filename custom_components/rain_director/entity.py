"""Entity helpers for the Rain Director integration."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import RainDirectorCoordinator


def make_device_info(entry_id: str, host: str) -> DeviceInfo:
    """Build the shared device metadata for this integration."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name="Rain Director",
        configuration_url=f"http://{host}",
    )


class RainDirectorEntity(CoordinatorEntity[RainDirectorCoordinator]):
    """Base class for Rain Director entities.

    Every entity except the connectivity sensor itself goes unavailable
    the moment the bus connection drops, rather than showing a stale
    last-known value with no indication anything's wrong.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: RainDirectorCoordinator, entry_id: str, unique_key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{unique_key}"
        self._attr_device_info = make_device_info(entry_id, coordinator.host)

    @property
    def available(self) -> bool:
        """Return whether the bus connection is currently up."""
        return self.coordinator.data.connected
