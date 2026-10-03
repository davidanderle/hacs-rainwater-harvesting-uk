#!/usr/bin/env python3
"""
Decode/filter the RS485 traffic sniffed from a rainwater harvesting
system (valve controller <-> LED controller <-> button controller),
forwarded over TCP by an EW11A RS485-to-WiFi bridge.

Usage:
    nc 192.168.1.46 8899 | python3 rainwater_level_filter.py

--------------------------------------------------------------------------
Frame format (reverse engineered, not a documented protocol)
--------------------------------------------------------------------------

    <PPAADDDD...CC

    <      start-of-frame marker. Its ASCII value (0x3C) is folded into
           the checksum as the seed, even though it isn't repeated in
           the checksummed payload itself.
    PP     2-char frame "type": 10, 20, 30 or 40.
    AA     2-char device address on the bus (53, 12, 33, ... seen so far).
    DDDD.. payload, hex-encoded bytes, length depends on frame type.
    CC     2-char checksum: seed 0x3C, XOR the ASCII byte value of every
           character from PP onward (i.e. everything except the leading
           '<' and the checksum itself), rendered as lowercase hex.

Frame types seen on the bus:

    1053   LED controller status. Pushed on its own, unsolicited -- no
           "1012.." poll frame precedes it.
    2053   Attic tank level sensor. Sent in reply to the "20123d" poll.
    30xx   A third device that is permanently absent in this
           installation. The "30123c" poll is sent regularly but no
           reply has ever been observed for it.
    4033   Button controller status. Sent in reply to the "40123b" poll.

Besides the '<...>' frames, the controller's own debug/log output shares
this same serial line. Some of it ("Write tank levels data to RAM",
"Sent to RAM") is plain chatter with no state in it and is ignored. The
rest is plain-text display/action messages (DISPLAY_MESSAGES,
ACTION_MESSAGES below) which are printed when they change, and anything
unrecognised, which is printed as UNKNOWN TEXT. There is also a JSON
blob it prints while updating its
internal tank-level state, e.g.:

    {"tanklevels":{"top":"0","bottom":"0","state":"1","r":"58","m":"21"}}

"state" is the rain director's operating mode -- known codes are in
STATE_NAMES below. "r"/"m" are still unconfirmed, possibly rainwater/
mains fill timeout counters. A second message shows up once, at the
end of commissioning:

    {"commisiondata":{"drain":"0","mains":"43","rainwater":"90"}}

Poll/response pairing:
    20123d, 30123c and 40123b are poll frames sent by the bus master.
    We hold a poll frame until we see a same-type reply, then print the
    pair together. 1053.. frames need no such pairing and are printed
    on their own whenever they change.

Change filtering:
    Only frames that differ from the last frame seen for that
    type+address are printed, so a quiet bus produces no output.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


# ==========================================================================
# Checksum
# ==========================================================================

def checksum_ok(packet: str) -> bool:
    """Validate the trailing 2-char XOR checksum of a frame.

    `packet` is the frame with its leading '<' already stripped off, e.g.
    "20531c8062". The checksum seed (0x3C) stands in for that stripped
    '<', then every remaining character except the checksum itself is
    XORed in by its ASCII value.
    """
    if len(packet) < 4:
        return False

    payload, received = packet[:-2], packet[-2:].lower()

    checksum = 0x3C
    for ch in payload:
        checksum ^= ord(ch)

    return f"{checksum:02x}" == received


# ==========================================================================
# Flag decoding
# ==========================================================================

def decode_flags(value: int, flags: dict[int, str]) -> list[str]:
    """Names of every bit set in `value`, in the order given by `flags`."""
    return [name for bit, name in flags.items() if value & bit]


BUTTON_FLAGS = {
    0b0001: "Drop",
    0b0010: "Tap",
    0b0100: "Holiday",
    0b1000: "Recycle",
}

# The LED byte is described in the OP's notes as occupying bits 16-20 of
# a combined (led_byte << 16 | misc_byte) value, distinct from the
# Engineering-mode bit living in the low byte. See the module docstring
# in the README/chat writeup for how this was derived from example frames.
LED_FLAGS = {
    0b00001 << 16: "Drop LED",
    0b00010 << 16: "No rainwater LED",
    0b00100 << 16: "Tap LED",
    0b01000 << 16: "Holiday LED",
    0b10000 << 16: "Recycle LED",
    0b00000100: "Engineering mode LED",
}


# ==========================================================================
# Per-frame-type decoders
#
# Each decoder receives just the payload hex (the part of the frame
# after the 4-char type+address prefix and before the 2-char checksum)
# and returns a human-readable description, or None if it doesn't
# recognise the payload.
# ==========================================================================

def decode_led_status(data: str) -> Optional[str]:
    if len(data) < 4:
        return None
    combined = (int(data[0:2], 16) << 16) | int(data[2:4], 16)
    on = decode_flags(combined, LED_FLAGS)
    return f"LEDs on: {', '.join(on)}" if on else "LEDs: all off"


def decode_tank_level(data: str) -> Optional[str]:
    if len(data) < 4:
        return None
    level = int(data[0:2], 16)
    unknown = data[2:4]
    return f"ATTIC LEVEL = {level}% (b[2]=0x{unknown})"


def decode_buttons(data: str) -> Optional[str]:
    if len(data) < 2:
        return None
    value = int(data[0:2], 16)
    if value == 0:
        return "ALL BUTTONS RELEASED"
    pressed = decode_flags(value, BUTTON_FLAGS)
    if pressed:
        return f"BUTTON PRESS: {' + '.join(pressed)}"
    return f"BUTTON EVENT 0x{value:02X} (unrecognised)"


# 4-char type+address prefix -> (label, decoder)
PREFIX_DECODERS: dict[str, tuple[str, Callable[[str], Optional[str]]]] = {
    "1053": ("LED status", decode_led_status),
    "2053": ("Tank level", decode_tank_level),
    "4033": ("Button status", decode_buttons),
}

# Poll frames sent by the bus master -> the frame "type" (first 2 chars)
# of the reply each one expects.
POLL_FRAMES: dict[str, str] = {
    "20123d": "20",
    "30123c": "30",
    "40123b": "40",
}


# ==========================================================================
# The controller's own debug JSON (interleaved with the '<...>' frames)
# ==========================================================================

# Rain Director operating-state codes, decoded from "state" in the
# {"tanklevels": {...}} debug JSON. The raw number is always kept
# alongside the name in the output (not replaced by it), so a
# not-yet-mapped code still shows up as e.g. "state=7 (unknown)"
# instead of vanishing -- add it here as a one-line entry once known.
STATE_NAMES: dict[int, str] = {
    0: "mains mode idle", # Or mains mode filling?
    1: "rainwater mode idle",
    3: "holiday drain",
    4: "holiday fill",
    5: "recycling drain",
    9: "commissioning",
    13: "engineering mode",
}


def decode_tanklevels_json(obj: dict) -> Optional[str]:
    tl = obj.get("tanklevels")
    if not isinstance(tl, dict):
        return None

    parts = []
    for key in ("state", "r", "m"):
        if key not in tl:
            continue
        if key == "state":
            try:
                code = int(tl[key])
            except (TypeError, ValueError):
                parts.append(f"state={tl[key]}")
                continue
            parts.append(f"state={code} ({STATE_NAMES.get(code, 'unknown')})")
        else:
            parts.append(f"{key}={tl[key]}")
    return ", ".join(parts) if parts else None


def decode_commisiondata_json(obj: dict) -> Optional[str]:
    # "commisiondata" (single 's') is the device's own spelling, kept
    # verbatim to match what's actually on the wire. Seen once so far,
    # right at the end of commissioning: drain/mains/rainwater -- maybe
    # the r/m timeout thresholds, unconfirmed.
    cd = obj.get("commisiondata")
    if not isinstance(cd, dict):
        return None
    return ", ".join(f"{key}={value}" for key, value in cd.items()) or None


# Each decoder recognises its own top-level JSON key and returns None
# when that key isn't present, so handle_json can just try them in
# turn. Add a new message shape by writing one more decode_xxx_json()
# function and appending it here.
JSON_DECODERS: list[Callable[[dict], Optional[str]]] = [
    decode_tanklevels_json,
    decode_commisiondata_json,
]


# ==========================================================================
# Plain-text debug lines
# ==========================================================================

# "(display)" lines repeat continuously while that screen is showing.
DISPLAY_MESSAGES: dict[str, str] = {
    "Mains only (display)": "mains_only",
    "Normal (display)": "normal",
    "Holiday (display)": "holiday",
    "Refresh (display)": "refresh",
}

# One-shot lines announcing the action the controller just started.
ACTION_MESSAGES: dict[str, str] = {
    "Fill from mains": "fill_from_mains",
    "Fill from rainwater": "fill_from_rainwater",
    "Holiday mode drain tank": "holiday_drain_tank",
    "Refresh tank": "refresh_tank",
}

TEXT_CHATTER = frozenset({"Write tank levels data to RAM", "Sent to RAM"})


# ==========================================================================
# Stateful filter: pairs polls with replies, suppresses unchanged frames
# ==========================================================================

@dataclass
class BusFilter:
    last_seen: dict[str, str] = field(default_factory=dict)  # prefix -> last printed frame
    pending_poll: Optional[str] = None

    def _describe(self, packet: str) -> Optional[str]:
        decoder = PREFIX_DECODERS.get(packet[:4])
        if decoder is None:
            return None
        _, decode_fn = decoder
        return decode_fn(packet[4:-2])

    def _changed(self, key: str, packet: str) -> bool:
        if self.last_seen.get(key) == packet:
            return False
        self.last_seen[key] = packet
        return True

    def _print(self, text: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)

    def _emit(self, packet: str, note: Optional[str] = None) -> None:
        description = note or self._describe(packet)
        line = f"<{packet}"
        if description:
            line += f"\t# {description}"
        self._print(line)

    def handle_json(self, line: str) -> None:
        try:
            obj = json.loads(line)
        except ValueError:
            return  # not JSON -- one of the plain-text log lines, ignore

        # Key by the JSON's own top-level shape, so an unrelated future
        # debug message (different keys) doesn't get compared against
        # tank-level state and wrongly suppressed as "unchanged".
        key = "json:" + ",".join(sorted(obj.keys()))
        if not self._changed(key, line):
            return

        description = None
        for decode_fn in JSON_DECODERS:
            description = decode_fn(obj)
            if description is not None:
                break

        text = line if description is None else f"{line}\t# {description}"
        self._print(text)

    def handle_text(self, line: str) -> None:
        if line in TEXT_CHATTER:
            return

        if line in DISPLAY_MESSAGES:
            key, description = "text:display", f"display={DISPLAY_MESSAGES[line]}"
        elif line in ACTION_MESSAGES:
            key, description = "text:action", f"action={ACTION_MESSAGES[line]}"
        else:
            key, description = "text:unknown", "UNKNOWN TEXT"

        if self._changed(key, line):
            self._print(f"{line}\t# {description}")

    def handle(self, packet: str) -> None:
        # Outbound poll: hold it and wait for the matching reply.
        if packet in POLL_FRAMES:
            self.pending_poll = packet
            return

        frame_type = packet[:2]

        # 10-series frames are unsolicited pushes, independent of any
        # poll/response pairing. Keyed by full prefix (not just type) in
        # case more addresses of the same type ever show up.
        if frame_type == "10":
            if self._changed(packet[:4], packet):
                self._emit(packet)
            return

        # A reply to an outstanding poll?
        if self.pending_poll is not None:
            poll, self.pending_poll = self.pending_poll, None
            expected_type = POLL_FRAMES[poll]

            if frame_type != expected_type:
                self._emit(packet, note=f"UNEXPECTED RESPONSE TO <{poll}")
                return

            if not self._changed(packet[:4], packet):
                return

            self._print(f"<{poll}")
            self._emit(packet)
            return

        # A frame that arrived with no outstanding poll and isn't a
        # 10-series push. Not expected in normal operation, but don't
        # silently drop it.
        if self._changed(packet[:4], packet):
            self._emit(packet)


# ==========================================================================
# Line framing
#
# The bridge has been observed to terminate frames with a bare '\r',
# which Python's normal line iteration over sys.stdin does NOT split on
# (only '\n' does) -- so we scan for either ourselves, tolerant of
# frames arriving split across multiple TCP reads.
# ==========================================================================

_LINE_SPLIT_RE = re.compile(r"[\r\n]")


def iter_lines(stream, chunk_size: int = 4096):
    buffer = ""
    for chunk in iter(lambda: stream.read(chunk_size), ""):
        buffer += chunk
        *lines, buffer = _LINE_SPLIT_RE.split(buffer)
        yield from lines
    if buffer:
        yield buffer


# ==========================================================================
# Main
# ==========================================================================

def main() -> None:
    bus_filter = BusFilter()

    for line in iter_lines(sys.stdin):
        line = line.strip()
        if not line:
            continue

        if line.startswith("<"):
            packet = line[1:]
            if not checksum_ok(packet):
                bus_filter._print(f"BAD CHECKSUM: <{packet}")
                continue
            bus_filter.handle(packet)
            continue

        if line.startswith("{"):
            bus_filter.handle_json(line)
            continue

        # Plain-text firmware debug line (display/action messages,
        # chatter, or something not seen before).
        bus_filter.handle_text(line)


if __name__ == "__main__":
    main()