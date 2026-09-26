"""Sensor platform for Rain Director."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RainDirectorCoordinator
from .entity import RainDirectorEntity

_COMMISSION_FIELDS = ("commission_drain", "commission_mains", "commission_rainwater")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Rain Director sensor entities for one config entry."""
    coordinator: RainDirectorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[RainDirectorEntity] = [
        TankLevelSensor(coordinator, entry.entry_id),
        ModeSensor(coordinator, entry.entry_id),
        RValueSensor(coordinator, entry.entry_id),
        MValueSensor(coordinator, entry.entry_id),
    ]
    entities += [
        CommissionSensor(coordinator, entry.entry_id, field) for field in _COMMISSION_FIELDS
    ]
    async_add_entities(entities)


class TankLevelSensor(RainDirectorEntity, SensorEntity):
    """The attic tank level -- the one most people install this for."""

    _attr_translation_key = "tank_level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "tank_level")

    @property
    def native_value(self) -> int | None:
        """Return the tank level percentage."""
        return self.coordinator.data.tank_level

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose the still-unidentified second payload byte."""
        return {"unknown_byte": self.coordinator.data.tank_level_unknown_byte}


class ModeSensor(RainDirectorEntity, SensorEntity):
    """Current Rain Director operating mode, decoded from the debug JSON."""

    _attr_translation_key = "mode"
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "mode")

    @property
    def native_value(self) -> str | None:
        """Return the mode name, or 'unknown_N' for an unmapped code."""
        data = self.coordinator.data
        if data.mode_code is None:
            return None
        return data.mode_name or f"unknown_{data.mode_code}"

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        """Expose the raw mode code alongside the friendly name."""
        return {"mode_code": self.coordinator.data.mode_code}


class RValueSensor(RainDirectorEntity, SensorEntity):
    """'r' field from the tanklevels JSON -- meaning unconfirmed."""

    _attr_translation_key = "r_value"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:help-circle-outline"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "r_value")

    @property
    def native_value(self) -> str | None:
        """Return the raw 'r' value."""
        return self.coordinator.data.r_value


class MValueSensor(RainDirectorEntity, SensorEntity):
    """'m' field from the tanklevels JSON -- meaning unconfirmed."""

    _attr_translation_key = "m_value"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:help-circle-outline"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "m_value")

    @property
    def native_value(self) -> str | None:
        """Return the raw 'm' value."""
        return self.coordinator.data.m_value


class CommissionSensor(RainDirectorEntity, SensorEntity):
    """One field of the one-time commissioning JSON blob.

    That message has only ever been observed once, at the end of
    commissioning, so on most installs this will just stay blank --
    that's expected, not a bug. Disabled by default so it doesn't
    clutter the entity list with permanently-empty sensors; enable it
    manually if you can trigger a re-commission and want to capture it.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: RainDirectorCoordinator, entry_id: str, field_name: str
    ) -> None:
        """Initialize entity. `field_name` must match a RainDirectorData attribute."""
        super().__init__(coordinator, entry_id, field_name)
        self._attr_translation_key = field_name
        self._field_name = field_name

    @property
    def native_value(self) -> str | None:
        """Return this commissioning field's last known value."""
        return getattr(self.coordinator.data, self._field_name)
