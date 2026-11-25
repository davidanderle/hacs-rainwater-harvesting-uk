"""Sensor platform for Rain Director."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RainDirectorCoordinator
from .entity import RainDirectorEntity

_COMMISSION_FIELDS = ("commission_drain", "commission_mains", "commission_rainwater")

# (WaterTotals field, state class, icon, entity category). "Saved" is the
# net figure and goes DOWN during a refresh/holiday drain, so it must be
# `total`: with `total_increasing` HA would read every decrease as a meter
# reset and corrupt the long-term statistics. The others only ever grow.
_WATER_SENSORS = (
    ("rain_saved", SensorStateClass.TOTAL, "mdi:water-check", None),
    ("rain_filled", SensorStateClass.TOTAL_INCREASING, "mdi:weather-pouring", None),
    ("mains_filled", SensorStateClass.TOTAL_INCREASING, "mdi:faucet", None),
    ("rain_drained", SensorStateClass.TOTAL_INCREASING, "mdi:water-minus", EntityCategory.DIAGNOSTIC),
)


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
        DisplayModeSensor(coordinator, entry.entry_id),
        LastActionSensor(coordinator, entry.entry_id),
        RValueSensor(coordinator, entry.entry_id),
        MValueSensor(coordinator, entry.entry_id),
        UnknownMessageSensor(coordinator, entry.entry_id),
    ]
    entities += [
        CommissionSensor(coordinator, entry.entry_id, field) for field in _COMMISSION_FIELDS
    ]
    entities += [
        WaterTotalSensor(coordinator, entry.entry_id, key, state_class, icon, category)
        for key, state_class, icon, category in _WATER_SENSORS
    ]
    async_add_entities(entities)


class _LatchedSensorMixin:
    """For sensors whose value the unit sends only rarely.

    The coordinator saves the last known value (see LATCHED_FIELDS in
    protocol.py) and restores it at startup, so these keep showing it --
    instead of flipping to unavailable whenever the bus connection is down,
    including the first moments after a Home Assistant restart. They read
    as unknown only if the unit has genuinely never sent the value.

    Must come before RainDirectorEntity in the bases so it wins the lookup.
    """

    @property
    def available(self) -> bool:
        """Always available: the value is a stored last-known one, not live."""
        return True


class WaterTotalSensor(RainDirectorEntity, SensorEntity):
    """One of the litre totals kept by the water meter (see water.py).

    These are persisted counters, so unlike the live bus sensors they stay
    available (showing the last total) while the bus connection is down.
    """

    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: RainDirectorCoordinator,
        entry_id: str,
        key: str,
        state_class: SensorStateClass,
        icon: str,
        category: EntityCategory | None,
    ) -> None:
        """Initialize entity. `key` must be a WaterTotals field or property."""
        super().__init__(coordinator, entry_id, f"water_{key}")
        self._attr_translation_key = f"water_{key}"
        self._key = key
        self._attr_state_class = state_class
        self._attr_icon = icon
        self._attr_entity_category = category

    @property
    def available(self) -> bool:
        """Always available: a stored total doesn't depend on the live bus."""
        return True

    @property
    def native_value(self) -> float:
        """Return the running total in litres."""
        return round(getattr(self.coordinator.meter.totals, self._key), 2)

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        """Expose the tank volume the litres were calculated with."""
        return {"tank_litres": self.coordinator.tank_litres}


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


class DisplayModeSensor(_LatchedSensorMixin, RainDirectorEntity, SensorEntity):
    """Screen currently shown on the control unit, from its '(display)' log lines.

    The unit only prints one when the screen changes, so the last known
    value is latched across restarts and bus outages.
    """

    _attr_translation_key = "display_mode"
    _attr_icon = "mdi:monitor"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "display_mode")

    @property
    def native_value(self) -> str | None:
        """Return the last display screen announced."""
        return self.coordinator.data.display_mode


class LastActionSensor(_LatchedSensorMixin, RainDirectorEntity, SensorEntity):
    """Last action the controller announced (fill, drain, refresh).

    Events are only announced when they happen, so the last known value is
    latched across restarts and bus outages.
    """

    _attr_translation_key = "last_action"
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "last_action")

    @property
    def native_value(self) -> str | None:
        """Return the last announced action."""
        return self.coordinator.data.last_action


def _as_count(value: str | None) -> int | None:
    """A counter from the unit's text field, or None if absent / not a whole number."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _RefillCountSensor(RainDirectorEntity, SensorEntity):
    """Base for the `r` / `m` counters of the tanklevels JSON.

    Found by running the system for a couple of weeks: they count how many
    times a rainwater (`r`) / mains (`m`) refill was triggered. The unit's
    own commissioning steps count too (a finished commissioning run ends at
    r=1, m=2), and both restart from 0 when it is commissioned again.

    Exposed as numeric `total_increasing` counters so Home Assistant can
    graph them and keep long-term statistics; a reset to 0 at
    re-commissioning is treated as a new counting cycle, which is correct.
    The exact rule for what the unit counts is not fully confirmed ("maybe
    more"), so the names say "refill count", not what triggers one.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"
    _field = ""

    def __init__(
        self, coordinator: RainDirectorCoordinator, entry_id: str, key: str
    ) -> None:
        """Initialize entity. `key` is kept as before so entity IDs and history survive."""
        super().__init__(coordinator, entry_id, key)
        self._attr_translation_key = key

    @property
    def native_value(self) -> int | None:
        """Return the counter, or None until the unit has reported it."""
        return _as_count(getattr(self.coordinator.data, self._field))


class RValueSensor(_RefillCountSensor):
    """'r': rainwater refill count."""

    _field = "r_value"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "r_value")


class MValueSensor(_RefillCountSensor):
    """'m': mains refill count."""

    _field = "m_value"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "m_value")


class UnknownMessageSensor(RainDirectorEntity, SensorEntity):
    """Count of bus frames/JSON lines that matched no known decoder.

    The reverse-engineering behind this integration is incomplete by
    nature, so this is the signal that something new showed up on the
    bus. Every increment is also logged as a warning (see
    coordinator.py), with the full frame/line text in the log; this
    entity is the glanceable, automatable version of the same thing --
    `last_message` holds the most recent example without needing to go
    dig through logs.
    """

    _attr_translation_key = "unknown_messages"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:help-rhombus-outline"

    def __init__(self, coordinator: RainDirectorCoordinator, entry_id: str) -> None:
        """Initialize entity."""
        super().__init__(coordinator, entry_id, "unknown_messages")

    @property
    def native_value(self) -> int:
        """Return the running count of unrecognized frames/JSON lines."""
        return self.coordinator.data.unknown_message_count

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the most recent unrecognized frame/line, and what preceded it."""
        data = self.coordinator.data
        return {
            "last_message": data.last_unknown_message,
            "context": list(data.last_unknown_context),
        }


class CommissionSensor(_LatchedSensorMixin, RainDirectorEntity, SensorEntity):
    """One field of the commissioning data the unit sends once, at power-up.

    It is never repeated, so the last value received is latched: it is saved
    and restored after a Home Assistant restart and the sensor stays
    available while the bus is down. It shows unknown only until the unit
    next powers up (or is re-commissioned) while the integration is
    connected. Disabled by default so it doesn't clutter the entity list;
    enable it to compare against the r/m values while debugging.
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
