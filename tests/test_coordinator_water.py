#!/usr/bin/env python3
"""Wiring tests for the coordinator's water accounting.

Loads the REAL coordinator.py (plus const/protocol/water) against the
minimal Home Assistant stand-ins in ha_stubs.py, and drives it with real bus
lines. It checks the integration's glue (load/save/baseline wiring), not
Home Assistant itself.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ha_stubs import load  # noqa: E402

Coordinator = load("coordinator").RainDirectorCoordinator


# -- helpers --------------------------------------------------------------------

def frame(payload):
    checksum = 0x3C
    for ch in payload:
        checksum ^= ord(ch)
    return "<" + payload + f"{checksum:02x}"


def level(pct):
    return frame("2053" + f"{pct:02x}" + "80")


LED_RAIN = frame("1053" + "0101")    # Drop LED + rain filling
LED_MAINS = frame("1053" + "0102")   # Drop LED + mains filling
LED_IDLE = frame("1053" + "0100")    # Drop LED only
MODE_NORMAL = '{"tanklevels":{"top":"50","bottom":"0","state":"1","r":"0","m":"0"}}'
MODE_REFRESH = '{"tanklevels":{"top":"50","bottom":"0","state":"5","r":"0","m":"0"}}'
MODE_COMMISSION = '{"tanklevels":{"top":"50","bottom":"0","state":"9","r":"1","m":"1"}}'


def make(tank_litres=100):
    return Coordinator(object(), "10.0.0.1", 8899, "entry1", tank_litres)


def feed(c, *lines):
    for line in lines:
        c._handle_line(line)


def totals(c):
    return c.meter.totals


# -- tests -----------------------------------------------------------------------

def test_store_key_is_per_entry():
    assert make()._store.key == "rainwater_harvesting.entry1.water"


def test_rain_fill_from_real_bus_lines_is_counted():
    c = make()
    feed(c, MODE_NORMAL, LED_RAIN, level(50))
    feed(c, *[level(p) for p in range(51, 61)])
    assert totals(c).rain_filled == 10.0
    assert totals(c).rain_saved == 10.0


def test_mains_fill_is_counted_separately():
    c = make()
    feed(c, MODE_NORMAL, LED_MAINS, level(50))
    feed(c, *[level(p) for p in range(51, 56)])
    assert totals(c).mains_filled == 5.0
    assert totals(c).rain_filled == 0.0


def test_changes_are_persisted_with_a_delay_not_on_every_step():
    c = make()
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(51), level(52))
    store = c._store
    assert len(store.delayed) == 2  # one per counted change (baseline excluded)
    data, delay = store.delayed[-1]
    assert data["rain_filled"] == 2.0
    assert delay == 30


def test_nothing_is_saved_when_nothing_changes():
    c = make()
    feed(c, MODE_NORMAL, LED_IDLE, level(60), level(59), level(58))  # house use only
    assert c._store.delayed == []


def test_commissioning_fill_is_not_counted():
    c = make()
    feed(c, MODE_COMMISSION, LED_RAIN, level(50))
    feed(c, *[level(p) for p in range(51, 70)])
    assert totals(c).rain_filled == 0.0


def test_refresh_drain_then_refill_nets_to_zero_via_the_bus():
    c = make()
    c.meter.totals = totals(c).__class__(rain_filled=10.0)  # start with 10 L saved
    feed(c, MODE_NORMAL, LED_IDLE, level(10))
    feed(c, MODE_REFRESH, *[level(p) for p in range(9, -1, -1)])  # drain 10 -> 0
    assert totals(c).rain_drained == 10.0
    assert totals(c).rain_saved == 0.0
    feed(c, MODE_NORMAL, LED_RAIN, *[level(p) for p in range(1, 11)])  # refill 0 -> 10
    assert totals(c).rain_saved == 10.0


def test_disconnect_resets_the_baseline_so_reconnect_is_not_a_fill():
    c = make()
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(51))
    assert totals(c).rain_filled == 1.0
    c._set_connected(True)
    c._set_connected(False)  # bridge drops; level moves a lot while away
    feed(c, level(80))  # first reading after reconnect: baseline only
    assert totals(c).rain_filled == 1.0
    feed(c, level(81))
    assert totals(c).rain_filled == 2.0


def test_tank_size_is_applied():
    c = make(tank_litres=80)
    assert c.tank_litres == 80.0
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(60))
    assert totals(c).rain_filled == 8.0


def test_saved_totals_are_restored_and_built_upon():
    c = make()
    c._store.loaded = {"rain_filled": 12.5, "mains_filled": 3.0, "rain_drained": 2.0}
    asyncio.run(c.async_load_totals())
    assert totals(c).rain_saved == 10.5
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(51))
    assert totals(c).rain_filled == 13.5


def test_empty_or_corrupt_store_starts_from_zero():
    for stored in (None, "junk", {"rain_filled": "x"}):
        c = make()
        c._store.loaded = stored
        asyncio.run(c.async_load_totals())
        assert totals(c).rain_filled == 0.0


def test_stop_flushes_totals_to_storage():
    c = make()
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(55))
    asyncio.run(c.async_stop())
    assert c._store.saved == {"rain_filled": 5.0, "mains_filled": 0.0, "rain_drained": 0.0}


def test_normal_snapshot_updates_still_flow_to_entities():
    c = make()
    feed(c, MODE_NORMAL, LED_RAIN, level(50), level(51))
    assert c.data.tank_level == 51
    assert c.data.mode_code == 1
    assert c.updates  # entities are notified


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
