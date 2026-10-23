"""Water accounting for the attic header tank.

Turns changes in the tank level (%) into litres, attributes them using
the unit's own indicator lights, and keeps three running totals:

    rain_filled    litres the tank was topped up with from rainwater
    mains_filled   litres the tank was topped up with from the mains
    rain_drained   litres drained back out in a holiday/refresh cycle

and one derived figure:

    rain_saved = rain_filled - rain_drained

"Saved" is the rainwater delivered into the tank, i.e. mains water that
did not have to be used. A refresh or holiday cycle drains the tank and
later pushes fresh rainwater back in; that refill is not a saving, so the
drain is subtracted and the refill adds it back -- a full cycle nets to
(about) zero, as it should. For the same reason `rain_saved` can go DOWN,
which is why it must be a `total` (not `total_increasing`) sensor in HA.

What is deliberately NOT done: attributing *consumption* to a source.
That would need a model of what is mixed together in the tank at any
moment, and would be wrong far more often than it is right. Only the
inflow by source (read straight off the filling lights) and the drains
(read off the unit's mode) are counted.

Known limits, all small:
  * the level moves in 1% steps, so counts are accurate to ~1% of the tank
  * water drawn by the house *while* a fill is running hides part of that
    fill (only the net rise is visible)
  * the unit reports its mode about every 7 s, so the first/last few
    percent of a drain can be missed
  * a drain is assumed to be rainwater; if mains backup water was in the
    tank at the time, `rain_saved` dips slightly too low

This module has no Home Assistant imports on purpose.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

# Operating states (the "state" field of the tanklevels debug JSON) in
# which level changes are NOT real household water movements:
#   9  commissioning            13  engineering mode (valve tests)
#   15 commissioning drain
EXCLUDED_MODES: frozenset[int] = frozenset({9, 13, 15})

# States in which a FALLING level is the unit emptying the tank back out
# (holiday drain / refresh drain) rather than the house drawing water.
# NOTE: these codes come from the original reverse-engineering and are not
# yet independently confirmed -- the "Rainwater drained" sensor exists so
# this can be checked: after a refresh it should rise by about one tank.
DRAIN_MODES: frozenset[int] = frozenset({3, 5})


@dataclass(frozen=True)
class WaterTotals:
    """Cumulative litres. Immutable; every update returns a new instance."""

    rain_filled: float = 0.0
    mains_filled: float = 0.0
    rain_drained: float = 0.0

    @property
    def rain_saved(self) -> float:
        return self.rain_filled - self.rain_drained

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "WaterTotals":
        """Rebuild from stored data, ignoring anything missing or malformed."""
        if not isinstance(data, dict):
            return cls()
        values: dict[str, float] = {}
        for name in ("rain_filled", "mains_filled", "rain_drained"):
            try:
                value = float(data.get(name, 0.0))
            except (TypeError, ValueError):
                value = 0.0
            values[name] = value if value == value else 0.0  # NaN -> 0
        return cls(**values)


def apply_level_change(
    totals: WaterTotals,
    litres: float,
    led_flags: Iterable[str],
    mode_code: Optional[int],
) -> WaterTotals:
    """Return `totals` updated for one level change of `litres` (+ rise, - fall)."""
    # Until the first state report arrives we can't tell commissioning or
    # engineering-mode movements from real ones, so don't guess.
    if mode_code is None or mode_code in EXCLUDED_MODES or litres == 0:
        return totals

    flags = set(led_flags)

    if litres > 0:
        rain, mains = "rain_filling" in flags, "mains_filling" in flags
        # Both lights at once would make the source ambiguous: skip it
        # rather than attribute litres to the wrong one.
        if rain and not mains:
            return WaterTotals(
                totals.rain_filled + litres, totals.mains_filled, totals.rain_drained
            )
        if mains and not rain:
            return WaterTotals(
                totals.rain_filled, totals.mains_filled + litres, totals.rain_drained
            )
        return totals

    if mode_code in DRAIN_MODES:
        return WaterTotals(
            totals.rain_filled, totals.mains_filled, totals.rain_drained - litres
        )

    return totals  # a fall in normal use: the house drawing water, not counted


class WaterMeter:
    """Feeds successive tank-level readings into the totals."""

    def __init__(self, tank_litres: float, totals: Optional[WaterTotals] = None) -> None:
        if not tank_litres > 0:
            raise ValueError("tank_litres must be positive")
        self.tank_litres = float(tank_litres)
        self.totals = totals or WaterTotals()
        self._last_level: Optional[float] = None

    def reset_baseline(self) -> None:
        """Forget the last level, e.g. after the bus connection drops.

        The next reading only sets a new baseline instead of being compared
        with a possibly long-stale one, so a reconnect can never register
        as a huge fill or drain.
        """
        self._last_level = None

    def update(
        self,
        level: Optional[float],
        led_flags: Iterable[str],
        mode_code: Optional[int],
    ) -> bool:
        """Process one reading. Returns True if the totals changed."""
        if level is None:
            return False
        previous, self._last_level = self._last_level, level
        if previous is None or level == previous:
            return False

        litres = (level - previous) * self.tank_litres / 100.0
        updated = apply_level_change(self.totals, litres, led_flags, mode_code)
        if updated == self.totals:
            return False
        self.totals = updated
        return True
