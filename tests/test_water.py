#!/usr/bin/env python3
"""Tests for the water accounting module."""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "rainwater_harvesting")
)

from water import (  # noqa: E402
    DRAIN_MODES,
    EXCLUDED_MODES,
    WaterMeter,
    WaterTotals,
    apply_level_change,
)

RAIN = {"drop", "rain_filling"}
MAINS = {"drop", "mains_filling"}
IDLE = {"drop"}
NORMAL = 1
REFRESH_DRAIN = 5
HOLIDAY_DRAIN = 3


def feed(meter, levels, leds, mode):
    """Feed a sequence of levels at fixed LED/mode state; return the meter."""
    for level in levels:
        meter.update(level, leds, mode)
    return meter


# -- rain / mains fills ---------------------------------------------------

def test_rain_fill_is_counted_as_rain_filled_and_saved():
    m = feed(WaterMeter(100), range(40, 61), RAIN, NORMAL)
    assert m.totals.rain_filled == 20.0
    assert m.totals.rain_saved == 20.0
    assert m.totals.mains_filled == 0.0


def test_mains_fill_is_counted_separately_and_does_not_count_as_saved():
    m = feed(WaterMeter(100), range(40, 51), MAINS, NORMAL)
    assert m.totals.mains_filled == 10.0
    assert m.totals.rain_filled == 0.0
    assert m.totals.rain_saved == 0.0


def test_rise_with_no_filling_light_is_not_counted():
    m = feed(WaterMeter(100), range(40, 51), IDLE, NORMAL)
    assert m.totals == WaterTotals()


def test_both_filling_lights_at_once_is_ambiguous_and_skipped():
    m = feed(WaterMeter(100), range(40, 51), {"rain_filling", "mains_filling"}, NORMAL)
    assert m.totals == WaterTotals()


def test_household_use_never_changes_any_total():
    m = feed(WaterMeter(100), range(80, 40, -1), IDLE, NORMAL)
    assert m.totals == WaterTotals()


def test_uneven_steps_add_the_full_rise():
    m = WaterMeter(100)
    for level in (40, 42, 45):
        m.update(level, RAIN, NORMAL)
    assert m.totals.rain_filled == 5.0


# -- tank size ------------------------------------------------------------

def test_tank_size_scales_litres():
    for litres, expected in ((100, 10.0), (80, 8.0), (250, 25.0)):
        m = feed(WaterMeter(litres), (50, 60), RAIN, NORMAL)
        assert m.totals.rain_filled == expected, litres


def test_non_positive_tank_size_is_rejected():
    for bad in (0, -5):
        try:
            WaterMeter(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted tank_litres={bad}")


# -- refresh / holiday cycles: the negative-saving rule --------------------

def test_drain_subtracts_from_saved():
    m = WaterMeter(100, WaterTotals(rain_filled=60.0))
    feed(m, range(60, 0, -1), IDLE, REFRESH_DRAIN)
    assert m.totals.rain_drained == 59.0
    assert m.totals.rain_saved == 1.0


def test_full_refresh_cycle_nets_to_zero():
    """Drain 100 -> 0 in refresh, then rainwater pushes it back to 100."""
    m = WaterMeter(100, WaterTotals(rain_filled=100.0))
    m.update(100, RAIN, NORMAL)  # baseline
    for level in range(99, -1, -1):
        m.update(level, IDLE, REFRESH_DRAIN)
    assert m.totals.rain_saved == 0.0  # counted down as it drained
    for level in range(1, 101):
        m.update(level, RAIN, NORMAL)  # refill counted back up
    assert m.totals.rain_saved == 100.0
    assert m.totals.rain_drained == 100.0
    assert m.totals.rain_filled == 200.0
    # ...so the net is what it was before the cycle began:
    assert m.totals.rain_saved == 100.0


def test_holiday_drain_also_subtracts():
    m = feed(WaterMeter(100, WaterTotals(rain_filled=50.0)), range(50, 0, -1), IDLE, HOLIDAY_DRAIN)
    assert m.totals.rain_drained == 49.0


def test_saved_may_legitimately_go_negative():
    # a drain of water that was in the tank before counting began
    m = feed(WaterMeter(100), range(50, 0, -1), IDLE, REFRESH_DRAIN)
    assert m.totals.rain_saved == -49.0


def test_falls_in_mains_only_mode_are_household_use():
    m = feed(WaterMeter(100), range(80, 40, -1), IDLE, 0)
    assert m.totals == WaterTotals()


# -- modes that must never count --------------------------------------------

def test_commissioning_and_engineering_movements_are_ignored():
    for mode in EXCLUDED_MODES:
        m = feed(WaterMeter(100), range(40, 70), RAIN, mode)
        assert m.totals == WaterTotals(), mode
        m = feed(WaterMeter(100), range(70, 40, -1), IDLE, mode)
        assert m.totals == WaterTotals(), mode


def test_unknown_mode_is_not_guessed_at():
    m = feed(WaterMeter(100), range(40, 60), RAIN, None)
    assert m.totals == WaterTotals()


def test_excluded_and_drain_modes_do_not_overlap():
    assert not (EXCLUDED_MODES & DRAIN_MODES)


# -- baseline handling --------------------------------------------------------

def test_first_reading_only_sets_a_baseline():
    m = WaterMeter(100)
    assert m.update(70, RAIN, NORMAL) is False
    assert m.totals == WaterTotals()


def test_reconnect_cannot_register_as_a_fill():
    m = WaterMeter(100)
    feed(m, (50, 51), RAIN, NORMAL)
    assert m.totals.rain_filled == 1.0
    m.reset_baseline()  # bus dropped; level moved a lot while away
    m.update(90, RAIN, NORMAL)  # first reading after reconnect
    assert m.totals.rain_filled == 1.0
    m.update(91, RAIN, NORMAL)
    assert m.totals.rain_filled == 2.0


def test_none_level_is_ignored_and_keeps_the_baseline():
    m = WaterMeter(100)
    m.update(50, RAIN, NORMAL)
    assert m.update(None, RAIN, NORMAL) is False
    m.update(51, RAIN, NORMAL)
    assert m.totals.rain_filled == 1.0


def test_update_reports_whether_totals_changed():
    m = WaterMeter(100)
    m.update(50, RAIN, NORMAL)
    assert m.update(51, RAIN, NORMAL) is True
    assert m.update(51, RAIN, NORMAL) is False  # same level
    assert m.update(50, IDLE, NORMAL) is False  # household use


# -- persistence ---------------------------------------------------------------

def test_totals_round_trip_through_dict():
    t = WaterTotals(12.5, 3.25, 1.0)
    assert WaterTotals.from_dict(t.to_dict()) == t


def test_from_dict_tolerates_missing_or_bad_data():
    assert WaterTotals.from_dict(None) == WaterTotals()
    assert WaterTotals.from_dict("junk") == WaterTotals()
    assert WaterTotals.from_dict({"rain_filled": "x"}) == WaterTotals()
    assert WaterTotals.from_dict({"rain_filled": float("nan")}) == WaterTotals()
    assert WaterTotals.from_dict({"rain_filled": "7"}).rain_filled == 7.0


def test_apply_level_change_is_pure():
    t = WaterTotals(1.0, 2.0, 3.0)
    apply_level_change(t, 5.0, RAIN, NORMAL)
    assert t == WaterTotals(1.0, 2.0, 3.0)


if __name__ == "__main__":
    failures = 0
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
