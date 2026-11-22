#!/usr/bin/env python3
"""Tests for latching the last known value of rarely-sent bus messages.

The Rain Director sends its commissioning data only once, at power-up, and
its display / action lines only when something happens. After a Home
Assistant restart those sensors used to show unknown (and unavailable
until the bridge reconnected), because the unit never repeats them. They
are now saved and restored, and stay available while the bus is down.

Loads the REAL protocol.py / coordinator.py / sensor.py against the minimal
Home Assistant stand-ins in ha_stubs.py.
"""

import asyncio
import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ha_stubs import load  # noqa: E402

protocol = load("protocol")
sensor = load("sensor")
Coordinator = load("coordinator").RainDirectorCoordinator

COMMISSION = '{"commisiondata":{"drain":"0","mains":"73","rainwater":"100"}}'
MODE_NORMAL = '{"tanklevels":{"top":"50","bottom":"0","state":"1","r":"1","m":"2"}}'
STORED = {
    "commission_drain": "0",
    "commission_mains": "73",
    "commission_rainwater": "100",
    "display_mode": "normal",
    "last_action": "fill_from_rainwater",
}
LATCHED_SENSORS = (
    ("DisplayModeSensor", ()),
    ("LastActionSensor", ()),
    ("CommissionSensor", ("commission_drain",)),
    ("CommissionSensor", ("commission_mains",)),
    ("CommissionSensor", ("commission_rainwater",)),
)


def frame(payload):
    checksum = 0x3C
    for ch in payload:
        checksum ^= ord(ch)
    return "<" + payload + f"{checksum:02x}"


def make():
    return Coordinator(object(), "10.0.0.1", 8899, "entry1", 100)


def feed(c, *lines):
    for line in lines:
        c._handle_line(line)


def restore(c, stored):
    c._latched_store.loaded = stored
    asyncio.run(c.async_load_latched())


def build(name, args, coordinator):
    return getattr(sensor, name)(coordinator, "entry1", *args)


# -- the pure helpers ----------------------------------------------------------------

def test_latched_fields_are_real_string_fields():
    names = {f.name for f in dataclasses.fields(protocol.RainDirectorData)}
    assert set(protocol.LATCHED_FIELDS) <= names
    blank = protocol.RainDirectorData()
    assert all(getattr(blank, f) is None for f in protocol.LATCHED_FIELDS)


def test_latched_fields_are_exactly_the_rarely_sent_ones():
    assert set(protocol.LATCHED_FIELDS) == {
        "commission_drain", "commission_mains", "commission_rainwater",
        "display_mode", "last_action",
    }


def test_latched_values_round_trip():
    data = protocol.restore_latched(protocol.RainDirectorData(), STORED)
    assert protocol.latched_values(data) == STORED


def test_restore_ignores_junk_and_keeps_good_values():
    base = protocol.RainDirectorData()
    for junk in (None, "junk", 5, [], {}):
        assert protocol.restore_latched(base, junk) == base
    mixed = protocol.restore_latched(
        base, {"display_mode": "normal", "last_action": 7, "commission_mains": None}
    )
    assert mixed.display_mode == "normal"
    assert mixed.last_action is None and mixed.commission_mains is None


def test_restore_cannot_smuggle_in_live_fields():
    data = protocol.restore_latched(
        protocol.RainDirectorData(),
        {"tank_level": "99", "connected": "yes", "mode_code": "1", **STORED},
    )
    assert data.tank_level is None
    assert data.connected is False
    assert data.mode_code is None


def test_restore_does_not_mutate_its_input():
    base = protocol.RainDirectorData()
    protocol.restore_latched(base, STORED)
    assert base == protocol.RainDirectorData()


# -- the coordinator -------------------------------------------------------------------

def test_stored_values_are_back_before_any_bus_traffic():
    c = make()
    restore(c, STORED)
    assert protocol.latched_values(c.data) == STORED
    assert c.data.connected is False  # restoring must not pretend we're live


def test_empty_or_corrupt_store_starts_blank():
    for stored in (None, "junk", {"display_mode": 3}):
        c = make()
        restore(c, stored)
        assert all(getattr(c.data, f) is None for f in protocol.LATCHED_FIELDS), stored


def test_new_commissioning_data_is_saved_promptly():
    c = make()
    feed(c, COMMISSION)
    assert len(c._latched_store.delayed) == 1
    saved, delay = c._latched_store.delayed[0]
    assert saved["commission_mains"] == "73" and saved["commission_rainwater"] == "100"
    # a short delay: these change rarely, and a hard power cut loses anything pending
    assert 0 < delay <= 5


def test_display_and_action_changes_are_saved():
    c = make()
    feed(c, "Normal (display)", "Fill from rainwater")
    assert c._latched_store.delayed[-1][0]["display_mode"] == "normal"
    assert c._latched_store.delayed[-1][0]["last_action"] == "fill_from_rainwater"


def test_unchanged_or_unrelated_updates_do_not_write():
    c = make()
    feed(c, "Normal (display)")
    writes = len(c._latched_store.delayed)
    feed(c, MODE_NORMAL, frame("2053" + "32" + "80"), frame("1053" + "0100"))
    assert len(c._latched_store.delayed) == writes


def test_a_fresh_bus_value_replaces_the_restored_one_and_is_saved():
    c = make()
    restore(c, STORED)
    feed(c, "Mains only (display)")
    assert c.data.display_mode == "mains_only"
    assert c._latched_store.delayed[-1][0]["display_mode"] == "mains_only"
    assert c.data.commission_mains == "73"  # untouched values stay latched


def test_stop_flushes_latched_values():
    c = make()
    restore(c, STORED)
    asyncio.run(c.async_stop())
    assert c._latched_store.saved == STORED


def test_latched_store_is_separate_from_the_water_store():
    c = make()
    assert c._latched_store.key == "rainwater_harvesting.entry1.latched"
    assert c._store.key == "rainwater_harvesting.entry1.water"


def test_full_power_cycle_keeps_the_values():
    """Run 1 sees the one-shot messages; run 2 (after a restart) must still show them."""
    run1 = make()
    feed(run1, COMMISSION, "Normal (display)", "Fill from rainwater")
    asyncio.run(run1.async_stop())

    run2 = make()  # a fresh start: nothing on the bus yet
    restore(run2, run1._latched_store.saved)
    for name, args in LATCHED_SENSORS:
        entity = build(name, args, run2)
        assert entity.native_value is not None, (name, args)
    assert build("CommissionSensor", ("commission_mains",), run2).native_value == "73"
    assert build("DisplayModeSensor", (), run2).native_value == "normal"
    assert build("LastActionSensor", (), run2).native_value == "fill_from_rainwater"


# -- availability ------------------------------------------------------------------------

def test_latched_sensors_stay_available_while_the_bus_is_down():
    c = make()
    assert c.data.connected is False
    for name, args in LATCHED_SENSORS:
        assert build(name, args, c).available is True, (name, args)


def test_latched_sensors_stay_available_after_the_bus_drops():
    c = make()
    c._set_connected(True)
    c._set_connected(False)
    for name, args in LATCHED_SENSORS:
        assert build(name, args, c).available is True, (name, args)


def test_never_seen_value_reads_as_none_not_a_made_up_one():
    c = make()
    for name, args in LATCHED_SENSORS:
        assert build(name, args, c).native_value is None, (name, args)


def test_live_sensors_still_go_unavailable_when_the_bus_is_down():
    """The change must not leak to sensors that are only meaningful live."""
    c = make()
    assert build("TankLevelSensor", (), c).available is False
    assert build("ModeSensor", (), c).available is False
    assert build("RValueSensor", (), c).available is False
    assert build("MValueSensor", (), c).available is False
    c._set_connected(True)
    assert build("TankLevelSensor", (), c).available is True


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
        except Exception as exc:  # surface wiring errors clearly
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
