"""Binary sensor platform for Rain Director."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RainDirectorCoordinator
from .entity import RainDirectorEntity

_LED_KEYS = ("drop", "no_rainwater", "tap", "holiday", "recycle", "engineering_mode")
_BUTTON_KEYS = ("drop", "tap", "holiday", "recycle")

# Default icons, one per LED flag -- shown anywhere HA displays the
# entity (entity list, more-info dialog, other cards) without needing
# an explicit `icon:` override, and match the dashboard card's choices.
_LED_ICONS: dict[str, str] = {
    "drop": "mdi:water",
    "tap": "mdi:water-pump",
    "no_rainwater": "mdi:water-off",
    "holiday": "mdi:palm-tree",
    "recycle": "mdi:recycle",
    "engineering_mode": "mdi:cog",
}
_DEFAULT_LED_ICON = "mdi:led-off"  # fallback if a new flag is ever added here first


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rain Director binary sensor entities for one config entry."""
    coordinator: RainDirectorCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[RainDirectorEntity] = [ConnectivitySensor(coordinator, entry.entry_id)]
    entities += [LedFlagSensor(coordinator, entry.entry_id, key) for key in _LED_KEYS]
    entities += [ButtonFlagSensor(coordinator, entry.entry_id, key) for key in _BUTTON_KEYS]
    async_add_entities(entities)


class ConnectivitySensor(RainDirectorEntity, BinarySensorEntity):
    """Whether the persistent TCP connection to the EW11A is up.

    Deliberately overrides `available` to always be True -- otherwise
    this entity would show 'unavailable' instead of 'off' exactly when
    it's most useful: while the bus connection is down.
    """

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "connectivity")

    @property
    def available(self) -> bool:
        """Always available -- this entity reports connectivity, it doesn't need it."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True while the bus connection is up."""
        return self.coordinator.data.connected


class LedFlagSensor(RainDirectorEntity, BinarySensorEntity):
    """One bit of the LED controller's status frame (1053)."""

    def __init__(
        self, coordinator: RainDirectorCoordinator, entry_id: str, flag_key: str
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, f"led_{flag_key}")
        self._attr_translation_key = f"led_{flag_key}"
        self._attr_icon = _LED_ICONS.get(flag_key, _DEFAULT_LED_ICON)
        self._flag_key = flag_key

    @property
    def is_on(self) -> bool:
        """Return True while this LED is lit."""
        return self._flag_key in self.coordinator.data.led_flags


class ButtonFlagSensor(RainDirectorEntity, BinarySensorEntity):
    """One bit of the front-panel button controller's status frame (4033)."""

    def __init__(
        self, coordinator: RainDirectorCoordinator, entry_id: str, flag_key: str
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, f"button_{flag_key}")
        self._attr_translation_key = f"button_{flag_key}"
        self._attr_icon = "mdi:gesture-tap-button"
        self._flag_key = flag_key

    @property
    def is_on(self) -> bool:
        """Return True while this button is held."""
        return self._flag_key in self.coordinator.data.button_flags
