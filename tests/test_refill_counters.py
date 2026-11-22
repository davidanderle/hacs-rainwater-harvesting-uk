#!/usr/bin/env python3
"""Tests for the r / m values as numeric refill counters.

Observed over two weeks of real use: `r` and `m` in the tank-levels JSON
count how many times a rainwater / mains refill was triggered. They were
exposed as raw text labelled "unconfirmed", which Home Assistant can't graph
or keep long-term statistics for. They are now numeric `total_increasing`
sensors. The entity keys (and so entity IDs and history) are unchanged.

Loads the REAL sensor.py / coordinator.py against the minimal Home Assistant
stand-ins in ha_stubs.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ha_stubs import load  # noqa: E402

sensor = load("sensor")
Coordinator = load("coordinator").RainDirectorCoordinator


def json_line(r, m, state=1, top=60):
    return (
        '{"tanklevels":{"top":"%s","bottom":"0","state":"%s","r":"%s","m":"%s"}}'
        % (top, state, r, m)
    )


def make():
    return Coordinator(object(), "10.0.0.1", 8899, "entry1", 100)


def r_sensor(c):
    return sensor.RValueSensor(c, "entry1")


def m_sensor(c):
    return sensor.MValueSensor(c, "entry1")


def test_values_are_numeric_not_text():
    c = make()
    c._handle_line(json_line(58, 21))
    assert r_sensor(c).native_value == 58 and isinstance(r_sensor(c).native_value, int)
    assert m_sensor(c).native_value == 21 and isinstance(m_sensor(c).native_value, int)


def test_zero_is_a_real_value_not_missing():
    """After commissioning starts both counters are 0; that must read 0, not unknown."""
    c = make()
    c._handle_line(json_line(0, 0, state=15))
    assert r_sensor(c).native_value == 0
    assert m_sensor(c).native_value == 0


def test_not_yet_seen_reads_as_none():
    c = make()
    assert r_sensor(c).native_value is None
    assert m_sensor(c).native_value is None


def test_non_numeric_value_reads_as_none_instead_of_crashing():
    c = make()
    c._handle_line('{"tanklevels":{"state":"1","r":"x","m":""}}')
    assert r_sensor(c).native_value is None
    assert m_sensor(c).native_value is None


def test_counters_follow_the_bus_through_a_commissioning_run_and_after():
    """The real trajectory: r 0,1,1 / m 0,1,2 during commissioning, then climbing."""
    c = make()
    seen = []
    for r, m, state in ((0, 0, 15), (1, 1, 9), (1, 2, 9), (1, 2, 1), (2, 2, 1), (2, 3, 1)):
        c._handle_line(json_line(r, m, state=state))
        seen.append((r_sensor(c).native_value, m_sensor(c).native_value))
    assert seen == [(0, 0), (1, 1), (1, 2), (1, 2), (2, 2), (2, 3)]


def test_reset_to_zero_at_recommissioning_is_representable():
    c = make()
    c._handle_line(json_line(58, 21))
    c._handle_line(json_line(0, 0, state=15))
    assert r_sensor(c).native_value == 0 and m_sensor(c).native_value == 0


def test_state_class_is_total_increasing():
    c = make()
    for entity in (r_sensor(c), m_sensor(c)):
        assert entity._attr_state_class == sensor.SensorStateClass.TOTAL_INCREASING


def test_no_unit_is_set_for_a_plain_count():
    c = make()
    for entity in (r_sensor(c), m_sensor(c)):
        assert getattr(entity, "_attr_native_unit_of_measurement", None) is None
        assert getattr(entity, "_attr_device_class", None) is None


def test_entity_identity_is_unchanged_so_history_and_ids_are_kept():
    c = make()
    assert r_sensor(c)._attr_unique_id == "entry1_r_value"
    assert m_sensor(c)._attr_unique_id == "entry1_m_value"
    assert r_sensor(c)._attr_translation_key == "r_value"
    assert m_sensor(c)._attr_translation_key == "m_value"


def test_counters_are_live_sensors_and_go_unavailable_with_the_bus():
    c = make()
    assert r_sensor(c).available is False
    c._set_connected(True)
    assert r_sensor(c).available is True and m_sensor(c).available is True


def test_still_diagnostic_and_given_a_counter_icon():
    c = make()
    for entity in (r_sensor(c), m_sensor(c)):
        assert entity._attr_entity_category == sensor.EntityCategory.DIAGNOSTIC
        assert entity._attr_icon == "mdi:counter"


def test_friendly_names_no_longer_claim_to_be_unconfirmed():
    import json

    names = json.load(
        open(os.path.join(os.path.dirname(__file__), "..", "custom_components",
                          "rainwater_harvesting", "strings.json"))
    )["entity"]["sensor"]
    assert names["r_value"]["name"] == "Rainwater refill count"
    assert names["m_value"]["name"] == "Mains refill count"


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
