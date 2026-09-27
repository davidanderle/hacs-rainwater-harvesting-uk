#!/usr/bin/env python3
"""Tests for the protocol module."""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "rainwater_harvesting")
)

from protocol import (  # noqa: E402
    BusFrameDecoder,
    RainDirectorData,
    checksum_ok,
    decode_flags,
    BUTTON_FLAGS,
    LED_FLAGS,
    STATE_NAMES,
)


def _frame(payload_without_checksum: str) -> str:
    """Build a valid frame from a payload, computing the trailing checksum."""
    checksum = 0x3C
    for ch in payload_without_checksum:
        checksum ^= ord(ch)
    return payload_without_checksum + f"{checksum:02x}"


def _feed(decoder: BusFrameDecoder, data: RainDirectorData, line: str):
    """Feed one line; return (possibly-updated data, notices)."""
    new_data, notices = decoder.process_line(line, data)
    return (new_data if new_data is not None else data), notices


# -- checksum -----------------------------------------------------------

def test_checksum_accepts_valid_frame():
    frame = _frame("20533a00")
    assert checksum_ok(frame) is True


def test_checksum_rejects_corrupted_frame():
    frame = _frame("20533a00")
    corrupted = frame[:-1] + ("0" if frame[-1] != "0" else "1")
    assert checksum_ok(corrupted) is False


def test_checksum_rejects_too_short_packet():
    assert checksum_ok("ab") is False


# -- flag decoding --------------------------------------------------------

def test_button_flag_decoding():
    assert decode_flags(0b0101, BUTTON_FLAGS) == frozenset({"drop", "holiday"})
    assert decode_flags(0, BUTTON_FLAGS) == frozenset()


def test_led_flag_decoding_combines_high_and_low_byte():
    # high byte bit 0 (0b00001 << 16) = drop LED, low byte bit 2 (0b100) = engineering mode
    combined = (0b00001 << 16) | 0b00000100
    assert decode_flags(combined, LED_FLAGS) == frozenset({"drop", "engineering_mode"})


# -- poll/reply pairing and tank level ------------------------------------

def test_poll_is_held_and_produces_no_update():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    new_data, notices = decoder.process_line("<20123d", data)
    assert new_data is None
    assert notices == []


def test_tank_level_reply_updates_state():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    data, _ = _feed(decoder, data, "<20123d")
    reply = _frame("2053" + "3a" + "00")  # level=0x3a=58, unknown byte 00
    data, notices = _feed(decoder, data, "<" + reply)
    assert data.tank_level == 58
    assert data.tank_level_unknown_byte == "00"
    assert notices == []


def test_unchanged_frame_is_suppressed():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    led_frame = _frame("1053" + "0104")
    data, _ = _feed(decoder, data, "<" + led_frame)
    new_data, notices = decoder.process_line("<" + led_frame, data)
    assert new_data is None
    assert notices == []


def test_unexpected_responder_is_flagged_without_updating_state():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    data, _ = _feed(decoder, data, "<30123c")  # poll the always-silent device
    reply = _frame("2053" + "3a" + "00")  # but a 20-series reply shows up instead
    new_data, notices = decoder.process_line("<" + reply, data)
    assert new_data is None
    assert len(notices) == 1
    assert "Unexpected response" in notices[0]


def test_bad_checksum_is_dropped_with_a_notice():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    corrupted = "2053" + "3a00" + "ff"
    new_data, notices = decoder.process_line("<" + corrupted, data)
    assert new_data is None
    assert "Bad checksum" in notices[0]


# -- unsolicited pushes ----------------------------------------------------

def test_led_push_needs_no_poll():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    led_frame = _frame("1053" + "0104")
    data, notices = _feed(decoder, data, "<" + led_frame)
    assert data.led_flags == frozenset({"drop", "engineering_mode"})
    assert notices == []


def test_button_push_updates_flags():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    btn_frame = _frame("4033" + "05")  # 0b0101 = drop + holiday
    data, notices = _feed(decoder, data, "<" + btn_frame)
    assert data.button_flags == frozenset({"drop", "holiday"})
    assert notices == []


# -- debug JSON ------------------------------------------------------------

def test_tanklevels_json_updates_mode_and_r_m():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    line = '{"tanklevels":{"top":"0","bottom":"0","state":"1","r":"58","m":"21"}}'
    data, notices = _feed(decoder, data, line)
    assert data.mode_code == 1
    assert data.mode_name == STATE_NAMES[1]
    assert data.r_value == "58"
    assert data.m_value == "21"
    assert notices == []


def test_unmapped_state_code_keeps_code_with_no_name():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    line = '{"tanklevels":{"state":"99"}}'
    data, _ = _feed(decoder, data, line)
    assert data.mode_code == 99
    assert data.mode_name is None


def test_commisiondata_json_is_captured():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    line = '{"commisiondata":{"drain":"0","mains":"43","rainwater":"90"}}'
    data, _ = _feed(decoder, data, line)
    assert data.commission_drain == "0"
    assert data.commission_mains == "43"
    assert data.commission_rainwater == "90"


def test_plain_debug_chatter_is_ignored():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    new_data, notices = decoder.process_line("Write tank levels data to RAM", data)
    assert new_data is None
    assert notices == []


def test_unparseable_json_is_ignored():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    new_data, notices = decoder.process_line("{not valid json", data)
    assert new_data is None
    assert notices == []


if __name__ == "__main__":
    failures = 0
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
