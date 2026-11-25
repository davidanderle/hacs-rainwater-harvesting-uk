#!/usr/bin/env python3
"""Tests for the "Flush inlet" action and for unknown-message context.

Seen while away from home: the unit logged "Flush inlet" and put a frame
<30153b on the bus (type 30, code 15, no payload -- the same shape as the
poll <30123c to the device that is never installed). One line can't show
whether that frame is a command from the controller or a reply from the
absent device; what surrounds it can. So every unknown message now carries
the raw lines that preceded it, in the log warning and as a sensor
attribute.

Loads the REAL protocol.py / coordinator.py / sensor.py against the minimal
Home Assistant stand-ins in ha_stubs.py.
"""

import dataclasses
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ha_stubs import load  # noqa: E402

protocol = load("protocol")
sensor = load("sensor")
coordinator_module = load("coordinator")
Decoder = protocol.BusFrameDecoder
Data = protocol.RainDirectorData

UNKNOWN_FRAME = "<30153b"
POLL_30 = "<30123c"


def frame(payload):
    checksum = 0x3C
    for ch in payload:
        checksum ^= ord(ch)
    return "<" + payload + f"{checksum:02x}"


def feed(decoder, data, *lines):
    """Feed lines; return (latest snapshot, every notice raised)."""
    notices = []
    for line in lines:
        new, got = decoder.process_line(line, data)
        notices += got
        data = new if new is not None else data
    return data, notices


# -- "Flush inlet" is a known action ----------------------------------------------

def test_flush_inlet_is_a_known_action():
    data, notices = feed(Decoder(), Data(), "Flush inlet")
    assert data.last_action == "flush_inlet"
    assert notices == []
    assert data.unknown_message_count == 0


def test_flush_inlet_repeats_are_filtered_like_other_actions():
    d = Decoder()
    data, _ = feed(d, Data(), "Flush inlet")
    new, notices = d.process_line("Flush inlet", data)
    assert new is None and notices == []


# -- context on unknown messages --------------------------------------------------------

def test_unknown_frame_carries_the_lines_that_preceded_it():
    lead_in = ["<20123d", frame("2053" + "32" + "80"), POLL_30]
    data, notices = feed(Decoder(), Data(), *lead_in, UNKNOWN_FRAME)
    assert data.unknown_message_count == 1
    assert data.last_unknown_message == UNKNOWN_FRAME
    assert data.last_unknown_context == tuple(lead_in)
    assert "preceded by" in notices[0] and POLL_30 in notices[0]
    assert notices[0].startswith("Unknown frame type, prefix 3015")


def test_context_does_not_include_the_unknown_line_itself_or_later_lines():
    d = Decoder()
    data, _ = feed(d, Data(), POLL_30, UNKNOWN_FRAME, "<40123b")
    assert UNKNOWN_FRAME not in data.last_unknown_context
    assert "<40123b" not in data.last_unknown_context
    assert data.last_unknown_context == (POLL_30,)


def test_context_is_capped_to_the_most_recent_lines():
    lines = [frame("1053" + f"{i:04x}") for i in range(40)]
    data, _ = feed(Decoder(), Data(), *lines, UNKNOWN_FRAME)
    n = protocol.CONTEXT_LINES
    assert len(data.last_unknown_context) == n
    assert data.last_unknown_context == tuple(lines[-n:])


def test_context_includes_lines_the_decoder_filtered_out():
    """Repeated/unchanged lines are dropped as state, but are still real context."""
    led = frame("1053" + "0100")
    data, _ = feed(Decoder(), Data(), led, led, led, led, UNKNOWN_FRAME)
    assert data.last_unknown_context == (led, led, led, led)


def test_first_line_ever_has_no_context_and_a_plain_notice():
    data, notices = feed(Decoder(), Data(), UNKNOWN_FRAME)
    assert data.last_unknown_context == ()
    assert notices == ["Unknown frame type, prefix 3015: <30153b"]


def test_every_kind_of_unknown_message_gets_context():
    lead = "<20123d"
    unknown_json = '{"somethingnew":{"x":"1"}}'
    unknown_text = "Something the unit has never said"
    unmapped_led = "<1053c00068"  # real frame: unmapped high-byte bits
    for line, expect in (
        (unknown_json, "Unknown JSON message shape"),
        (unknown_text, "Unknown text message"),
        (unmapped_led, "unmapped bits"),
    ):
        data, notices = feed(Decoder(), Data(), lead, line)
        assert data.last_unknown_context == (lead,), line
        assert expect in notices[0] and "preceded by" in notices[0], line


def test_known_messages_do_not_touch_the_context():
    d = Decoder()
    data, notices = feed(d, Data(), POLL_30, "Normal (display)", frame("1053" + "0100"))
    assert data.last_unknown_context == ()
    assert notices == []


def test_context_never_spans_a_reconnect():
    d = Decoder()
    feed(d, Data(), "<20123d", POLL_30)
    d.reset_connection()
    data, notices = feed(d, Data(), UNKNOWN_FRAME)
    assert data.last_unknown_context == ()
    assert "preceded by" not in notices[0]


def test_very_long_lines_are_truncated_in_context():
    long_line = '{"tanklevels":{' + "x" * 500 + "}}"
    data, _ = feed(Decoder(), Data(), long_line, UNKNOWN_FRAME)
    assert len(data.last_unknown_context[0]) <= protocol.CONTEXT_LINE_MAX


def test_context_is_immutable_data():
    assert Data().last_unknown_context == ()
    assert isinstance(Data().last_unknown_context, tuple)
    try:
        dataclasses.replace(Data(), last_unknown_context=("a",)).last_unknown_context.append("b")
    except AttributeError:
        return
    raise AssertionError("context should be an immutable tuple")


def test_newest_unknown_replaces_the_old_context():
    led = frame("1053" + "0100")  # type 10: never interacts with poll pairing
    d = Decoder()
    data, _ = feed(d, Data(), led, UNKNOWN_FRAME)
    assert data.last_unknown_context == (led,)
    other = frame("5099" + "ab")
    data, _ = feed(d, data, led, other)
    assert data.unknown_message_count == 2
    assert data.last_unknown_context == (led, UNKNOWN_FRAME, led)


def test_an_unexpected_response_inside_a_poll_window_also_carries_context():
    """If the frame next lands between a poll and its reply the warning differs
    ("Unexpected response", not "Unknown frame") -- it must carry context too."""
    d = Decoder()
    data, notices = feed(d, Data(), POLL_30, "<20123d", UNKNOWN_FRAME)
    assert len(notices) == 1
    assert notices[0].startswith("Unexpected response to poll <20123d")
    assert "preceded by" in notices[0] and POLL_30 in notices[0]
    assert data.unknown_message_count == 0  # unchanged behaviour: not counted


# -- reaching the log and the sensor ---------------------------------------------------------

def _coordinator():
    return coordinator_module.RainDirectorCoordinator(object(), "10.0.0.1", 8899, "entry1", 100)


def test_the_warning_that_reaches_the_log_includes_the_context():
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    capture = Capture()
    logger = logging.getLogger(coordinator_module.__name__)
    logger.addHandler(capture)
    try:
        c = _coordinator()
        for line in ("<20123d", POLL_30, UNKNOWN_FRAME):
            c._handle_line(line)
    finally:
        logger.removeHandler(capture)
    warnings = [m for m in capture.messages if "Unknown frame type" in m]
    assert len(warnings) == 1
    assert "<30153b" in warnings[0] and "<30123c" in warnings[0]


def test_sensor_exposes_the_context_as_an_attribute():
    c = _coordinator()
    for line in ("<20123d", POLL_30, UNKNOWN_FRAME):
        c._handle_line(line)
    entity = sensor.UnknownMessageSensor(c, "entry1")
    attrs = entity.extra_state_attributes
    assert attrs["last_message"] == UNKNOWN_FRAME
    assert attrs["context"] == ["<20123d", POLL_30]
    assert isinstance(attrs["context"], list)  # plain list for the state machine


def test_sensor_attribute_is_empty_before_anything_unknown():
    entity = sensor.UnknownMessageSensor(_coordinator(), "entry1")
    attrs = entity.extra_state_attributes
    assert attrs["last_message"] is None and attrs["context"] == []


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
