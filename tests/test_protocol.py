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
    unmapped_led_bits,
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


def test_unknown_frame_prefix_is_flagged_and_counted():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    unknown_frame = _frame("5099" + "ab")  # a type/address never seen before
    data, notices = _feed(decoder, data, "<" + unknown_frame)
    assert data.unknown_message_count == 1
    assert data.last_unknown_message == "<" + unknown_frame
    assert len(notices) == 1
    assert "Unknown frame type" in notices[0]


def test_unknown_json_shape_is_flagged_and_counted():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    line = '{"somethingnew":{"x":"1"}}'
    data, notices = _feed(decoder, data, line)
    assert data.unknown_message_count == 1
    assert data.last_unknown_message == line
    assert "Unknown JSON message shape" in notices[0]


def test_known_frames_do_not_increment_unknown_count():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    led_frame = _frame("1053" + "0104")
    data, _ = _feed(decoder, data, "<" + led_frame)
    assert data.unknown_message_count == 0


def test_repeated_unknown_frame_only_flagged_once_while_unchanged():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    unknown_frame = _frame("5099" + "ab")
    data, _ = _feed(decoder, data, "<" + unknown_frame)
    assert data.unknown_message_count == 1
    # same exact packet again -- existing per-prefix dedup should suppress it
    new_data, notices = decoder.process_line("<" + unknown_frame, data)
    assert new_data is None
    assert notices == []


def test_known_text_chatter_is_ignored():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    for line in ("Write tank levels data to RAM", "Sent to RAM"):
        new_data, notices = decoder.process_line(line, data)
        assert new_data is None
        assert notices == []


def test_display_message_sets_display_mode_once():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    data, notices = _feed(decoder, data, "Holiday (display)")
    assert data.display_mode == "holiday"
    assert data.unknown_message_count == 0
    assert notices == []
    new_data, _ = decoder.process_line("Holiday (display)", data)
    assert new_data is None


def test_action_message_sets_last_action():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    data, _ = _feed(decoder, data, "Fill from rainwater")
    assert data.last_action == "fill_from_rainwater"
    data, _ = _feed(decoder, data, "Refresh tank")
    assert data.last_action == "refresh_tank"
    assert data.unknown_message_count == 0


def test_unknown_text_is_flagged_once_while_unchanged():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    data, notices = _feed(decoder, data, "Something new happened")
    assert data.unknown_message_count == 1
    assert data.last_unknown_message == "Something new happened"
    assert "Unknown text message" in notices[0]
    new_data, notices = decoder.process_line("Something new happened", data)
    assert new_data is None
    assert notices == []


def test_reset_connection_clears_pending_poll_but_keeps_change_filter():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    reply = _frame("2053" + "3a00")
    data, _ = _feed(decoder, data, "<20123d")
    data, _ = _feed(decoder, data, "<" + reply)
    data, _ = _feed(decoder, data, "<30123c")  # poll left unanswered
    decoder.reset_connection()
    # A pending poll would make this 20-series frame an unexpected response.
    new_data, notices = decoder.process_line("<" + _frame("2053" + "3b00"), data)
    assert notices == []
    assert new_data.tank_level == 0x3b
    # Unchanged frame is still suppressed after the reset.
    new_data, notices = decoder.process_line("<" + _frame("2053" + "3b00"), new_data)
    assert new_data is None


def test_commissioning_and_engineering_messages_are_all_known():
    """Every text line from a real commissioning + engineering-mode capture."""
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    lines = [
        "Level sensor auto commission",
        "Auto commission - rainwater fill",
        "Auto commission - mains fill",
        "Auto commission complete",
        "Write commission data to RAM",
        "Engineering mode (display)",
        "Engineering mode solenoid test",
        "Exit engineering mode (display)",
    ]
    for line in lines:
        data, notices = _feed(decoder, data, line)
        assert notices == [], line
    assert data.unknown_message_count == 0
    assert data.last_action == "engineering_solenoid_test"
    assert data.display_mode == "exit_engineering_mode"


def test_commissioning_steps_are_reported_in_order():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    seen = []
    for line in (
        "Level sensor auto commission",
        "Auto commission - rainwater fill",
        "Auto commission - mains fill",
        "Auto commission complete",
    ):
        data, _ = _feed(decoder, data, line)
        seen.append(data.last_action)
    assert seen == [
        "commission_level_sensor",
        "commission_rainwater_fill",
        "commission_mains_fill",
        "commission_complete",
    ]


def test_commission_write_line_is_chatter_not_unknown():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    new_data, notices = decoder.process_line("Write commission data to RAM", data)
    assert new_data is None
    assert notices == []


def test_state_15_is_commissioning_drain():
    decoder = BusFrameDecoder()
    data = RainDirectorData()
    line = '{"tanklevels":{"top":"85","bottom":"0","state":"15","r":"0","m":"0"}}'
    data, _ = _feed(decoder, data, line)
    assert data.mode_code == 15
    assert data.mode_name == STATE_NAMES[15] == "commissioning_drain"


def test_unmapped_led_bits_are_reported_not_read_as_off():
    """Real frames from a commissioning run: bits the decoder has no name for."""
    for raw in ("1053c00068", "105340013e", "105340023d"):
        decoder = BusFrameDecoder()
        data = RainDirectorData()
        assert checksum_ok(raw), raw
        data, notices = _feed(decoder, data, "<" + raw)
        assert data.led_flags == frozenset(), raw
        assert data.unknown_message_count == 1, raw
        assert data.last_unknown_message == "<" + raw
        assert len(notices) == 1 and "unmapped bits" in notices[0], raw


def test_mapped_led_frames_do_not_trigger_unmapped_warning():
    # real frames: Drop LED; Drop + Engineering; Engineering only
    for raw in ("105301003a", "105301043e", "105300043f"):
        decoder = BusFrameDecoder()
        data = RainDirectorData()
        data, notices = _feed(decoder, data, "<" + raw)
        assert notices == [], raw
        assert data.unknown_message_count == 0, raw
        assert data.led_flags, raw


def test_unmapped_led_bits_value_is_exact():
    assert unmapped_led_bits("4001") == 0x400001
    assert unmapped_led_bits("4002") == 0x400002
    assert unmapped_led_bits("c000") == 0xC00000
    assert unmapped_led_bits("0104") == 0  # drop + engineering: fully mapped
    assert unmapped_led_bits("01") == 0  # too short to decode


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
