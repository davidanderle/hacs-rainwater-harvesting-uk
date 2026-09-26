"""Pure decoding logic for the Rain Director RS485 bus.

This is a direct port of the reverse-engineering work in the original
`traffic_decoder.py` sniffer script -- same checksum, same frame types,
same JSON messages, same change-filtering and poll/reply pairing rules.
The difference is that this module does no I/O and no printing: it just
turns bus lines into an immutable `RainDirectorData` snapshot, so it can
be unit tested on its own (see tests/test_protocol.py) and driven by
either a live socket (coordinator.py) or a recorded log file.

    <PPAADDDD...CC

    <      start-of-frame marker, folded into the checksum as a seed
           (0x3C) but not itself part of the checksummed payload.
    PP     2-char frame "type": 10, 20, 30 or 40.
    AA     2-char device address on the bus.
    DDDD.. payload, hex-encoded bytes, length depends on frame type.
    CC     2-char checksum: seed 0x3C, XOR every character from PP
           onward, rendered as lowercase hex.

Frame types:
    1053   LED controller status -- pushed unsolicited.
    2053   Attic tank level sensor -- reply to the "20123d" poll.
    30xx   A device that's permanently absent on this installation;
           polled regularly ("30123c") but never replies.
    4033   Button controller status -- reply to the "40123b" poll.

Besides '<...>' frames, the controller's own debug log shares the same
serial line. Most of it is chatter with no state in it. The one JSON
line worth keeping updates the tank-level/mode state, e.g.:

    {"tanklevels":{"top":"0","bottom":"0","state":"1","r":"58","m":"21"}}

"state" is the operating mode (STATE_NAMES below); "r"/"m" are still
unconfirmed, possibly rainwater/mains fill timeout counters. A second
message shows up once, at the end of commissioning:

    {"commisiondata":{"drain":"0","mains":"43","rainwater":"90"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional


# ==========================================================================
# Checksum
# ==========================================================================

def checksum_ok(packet: str) -> bool:
    """Validate the trailing 2-char XOR checksum of a frame.

    `packet` is the frame with its leading '<' already stripped, e.g.
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

def decode_flags(value: int, flags: dict[int, str]) -> frozenset[str]:
    """Keys of every bit set in `value`."""
    return frozenset(name for bit, name in flags.items() if value & bit)


BUTTON_FLAGS: dict[int, str] = {
    0b0001: "drop",
    0b0010: "tap",
    0b0100: "holiday",
    0b1000: "recycle",
}

# The LED byte occupies bits 16-20 of a combined (led_byte << 16 | misc_byte)
# value; the Engineering-mode bit lives separately, in the low (misc) byte.
LED_FLAGS: dict[int, str] = {
    0b00001 << 16: "drop",
    0b00010 << 16: "no_rainwater",
    0b00100 << 16: "tap",
    0b01000 << 16: "holiday",
    0b10000 << 16: "recycle",
    0b00000100: "engineering_mode",
}

# Rain Director operating-state codes, decoded from "state" in the
# {"tanklevels": {...}} debug JSON. Add a new code here as one line
# once it's confirmed; an unmapped code still surfaces as mode_code
# with mode_name=None rather than silently vanishing.
STATE_NAMES: dict[int, str] = {
    0: "mains_mode_idle",  # or mains mode filling?
    1: "rainwater_mode_idle",
    3: "holiday_drain",
    4: "holiday_fill",
    5: "recycling_drain",
    9: "commissioning",
    13: "engineering_mode",
}

# Poll frames sent by the bus master -> the frame "type" (first 2 chars)
# of the reply each one expects.
POLL_FRAMES: dict[str, str] = {
    "20123d": "20",
    "30123c": "30",
    "40123b": "40",
}


# ==========================================================================
# Public snapshot handed to Home Assistant
# ==========================================================================

@dataclass(frozen=True)
class RainDirectorData:
    """Latest known state of the bus, as seen by entities.

    Immutable: every update produces a new instance via `replace()`
    rather than mutating fields in place, so a coordinator can safely
    hand old snapshots to entities that are still rendering them.
    """

    connected: bool = False
    last_update: Optional[datetime] = None

    tank_level: Optional[int] = None
    tank_level_unknown_byte: Optional[str] = None

    led_flags: frozenset[str] = frozenset()
    button_flags: frozenset[str] = frozenset()

    mode_code: Optional[int] = None
    mode_name: Optional[str] = None
    r_value: Optional[str] = None
    m_value: Optional[str] = None

    commission_drain: Optional[str] = None
    commission_mains: Optional[str] = None
    commission_rainwater: Optional[str] = None


# ==========================================================================
# Stateful decoder: pairs polls with replies, suppresses unchanged frames
# ==========================================================================

class BusFrameDecoder:
    """Line-by-line decoder with the same change-filtering and poll/reply
    pairing rules as the original sniffer's `BusFilter`.

    Call `process_line()` once per line of bus traffic (or debug JSON).
    It returns `(None, notices)` when nothing entity-relevant changed,
    or `(new_snapshot, notices)` when it did. `notices` are plain-text
    diagnostics (bad checksum, unexpected responder) for the caller to
    log -- this class never logs or prints anything itself.
    """

    def __init__(self) -> None:
        self._last_seen: dict[str, str] = {}
        self._pending_poll: Optional[str] = None

    def process_line(
        self, line: str, data: RainDirectorData
    ) -> tuple[Optional[RainDirectorData], list[str]]:
        if line.startswith("<"):
            return self._process_frame(line[1:], data)
        if line.startswith("{"):
            return self._process_json(line, data)
        return None, []  # firmware debug chatter, e.g. "Sent to RAM" -- ignored

    # -- '<...>' frames ---------------------------------------------------

    def _process_frame(
        self, packet: str, data: RainDirectorData
    ) -> tuple[Optional[RainDirectorData], list[str]]:
        if not checksum_ok(packet):
            return None, [f"Bad checksum, dropping frame: <{packet}"]

        if packet in POLL_FRAMES:
            self._pending_poll = packet
            return None, []

        frame_type = packet[:2]
        prefix = packet[:4]

        # 10-series frames are unsolicited pushes: they don't consume a
        # pending poll, and need no pairing.
        if frame_type != "10" and self._pending_poll is not None:
            poll, self._pending_poll = self._pending_poll, None
            expected_type = POLL_FRAMES[poll]
            if frame_type != expected_type:
                return None, [
                    f"Unexpected response to poll <{poll}: got type "
                    f"{frame_type} (<{packet})"
                ]

        if self._last_seen.get(prefix) == packet:
            return None, []
        self._last_seen[prefix] = packet

        updates = self._decode_payload(prefix, packet[4:-2])
        if not updates:
            return None, []

        return replace(data, last_update=datetime.now(), **updates), []

    @staticmethod
    def _decode_payload(prefix: str, payload: str) -> dict:
        if prefix == "1053":
            return BusFrameDecoder._decode_led(payload)
        if prefix == "2053":
            return BusFrameDecoder._decode_tank_level(payload)
        if prefix == "4033":
            return BusFrameDecoder._decode_buttons(payload)
        return {}

    @staticmethod
    def _decode_led(data: str) -> dict:
        if len(data) < 4:
            return {}
        combined = (int(data[0:2], 16) << 16) | int(data[2:4], 16)
        return {"led_flags": decode_flags(combined, LED_FLAGS)}

    @staticmethod
    def _decode_tank_level(data: str) -> dict:
        if len(data) < 4:
            return {}
        return {
            "tank_level": int(data[0:2], 16),
            "tank_level_unknown_byte": data[2:4],
        }

    @staticmethod
    def _decode_buttons(data: str) -> dict:
        if len(data) < 2:
            return {}
        value = int(data[0:2], 16)
        return {"button_flags": decode_flags(value, BUTTON_FLAGS)}

    # -- debug JSON ---------------------------------------------------------

    def _process_json(
        self, line: str, data: RainDirectorData
    ) -> tuple[Optional[RainDirectorData], list[str]]:
        try:
            obj = json.loads(line)
        except ValueError:
            return None, []

        # Key by the JSON's own top-level shape, so an unrelated future
        # debug message (different keys) doesn't get compared against
        # tank-level state and wrongly suppressed as "unchanged".
        key = "json:" + ",".join(sorted(obj.keys()))
        if self._last_seen.get(key) == line:
            return None, []
        self._last_seen[key] = line

        updates: dict = {}
        tanklevels = obj.get("tanklevels")
        if isinstance(tanklevels, dict):
            updates.update(self._decode_tanklevels(tanklevels))

        # "commisiondata" (single 's') is the device's own spelling, kept
        # verbatim to match what's actually on the wire.
        commisiondata = obj.get("commisiondata")
        if isinstance(commisiondata, dict):
            updates.update(self._decode_commisiondata(commisiondata))

        if not updates:
            return None, []

        return replace(data, last_update=datetime.now(), **updates), []

    @staticmethod
    def _decode_tanklevels(tanklevels: dict) -> dict:
        updates: dict = {}
        if "state" in tanklevels:
            try:
                code = int(tanklevels["state"])
            except (TypeError, ValueError):
                code = None
            updates["mode_code"] = code
            updates["mode_name"] = STATE_NAMES.get(code) if code is not None else None
        if "r" in tanklevels:
            updates["r_value"] = tanklevels["r"]
        if "m" in tanklevels:
            updates["m_value"] = tanklevels["m"]
        return updates

    @staticmethod
    def _decode_commisiondata(commisiondata: dict) -> dict:
        updates: dict = {}
        if "drain" in commisiondata:
            updates["commission_drain"] = commisiondata["drain"]
        if "mains" in commisiondata:
            updates["commission_mains"] = commisiondata["mains"]
        if "rainwater" in commisiondata:
            updates["commission_rainwater"] = commisiondata["rainwater"]
        return updates
