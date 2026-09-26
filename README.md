[![hassfest](https://github.com/home-assistant/actions/workflows/hassfest.yaml/badge.svg)](...)
[![Tests](https://github.com/davidanderle/hacs-claber-myaquasolar-ble/actions/workflows/tests.yml/badge.svg)](https://github.com/davidanderle/hacs-rainwater-harvesting-uk/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

# RainWater Harvesting LTD Rain Director — RS485 Protocol & Home Assistant Integration

Fully reverse-engineered RS485 protocol for the [Rain Director](https://www.rainwaterharvesting.co.uk/product/rain-director/)
control unit, plus a HACS integration built on top of it. No public
protocol documentation or Home Assistant integration existed prior to
this work.

This project is an independent Home Assistant integration and is not
affiliated with, endorsed by, or sponsored by RainWater Harvesting LTD.
RainWater Harvesting LTD and the RainWater Harvesting LTD logo are
trademarks of their respective owners.

No control is implemented -- this is read-only monitoring, matching
what's actually feasible on this installation today.

## Contents

- `traffic_decoder.py` -- standalone sniffer/decoder script, run
  against the raw bus stream from the command line. Useful for
  debugging or for capturing new frame types before wiring them into
  the integration.
- `custom_components/rain_director/` -- the HACS integration itself.
- `tests/test_protocol.py` -- unit tests for the integration's decoder.

## The hardware setup

An Elfin EW11A RS485-to-WiFi bridge is wired onto the Rain Director's
bus (valve controller <-> LED controller <-> button controller <->
attic tank level sensor), configured in TCP-server mode. That exposes
the bus as a raw TCP stream: anything that opens a TCP connection to it
sees every message on the wire, unencrypted.

### Standalone: sniffing from the command line

```
nc 192.168.1.46 8899 | python3 traffic_decoder.py
```

This prints a human-readable, change-filtered log of bus traffic --
handy for debugging, or for capturing an unfamiliar frame before adding
a decoder for it. See the module docstring in `traffic_decoder.py` for
the full frame format writeup (checksum, frame types, the JSON debug
messages interleaved on the same line).

## Home Assistant integration

The EW11A only being reachable over raw TCP -- no MODBUS, nothing
pollable, no request/response protocol on a schedule -- ruled out both
a polling `DataUpdateCoordinator` and needing an MQTT broker in the
middle. Instead, the integration opens **one persistent TCP connection**
to the EW11A for as long as it's loaded and reads continuously, exactly
like the `nc` command above. That connection feeds a decoder ported
from `traffic_decoder.py` (checksum validation, frame parsing, JSON
handling, poll/reply pairing), which produces immutable state
snapshots; an entity's state only changes when a decoded value actually
differs from last time. The bus itself does roughly 100 messages/sec,
but the vast majority are re-announcements of state that hasn't moved,
so this filtering is what keeps Home Assistant's recorder database from
filling up with identical rows.

If the connection drops (WiFi blip, EW11A reboot), it reconnects with
exponential backoff (5s -> 10s -> 20s -> ... capped at 60s).

**One consequence worth knowing:** the EW11A generally only serves one
open TCP connection at a time in this mode. Once the integration is
running, it holds that slot -- you won't be able to run `nc` alongside
it to keep debugging on the wire without stopping the integration
first. If you ever need both running at once, or multiple independent
consumers of the same stream, the fix is a small always-on bridge
process that holds the one EW11A connection and republishes over MQTT
with retained topics; the integration deliberately doesn't do that
itself, since it's an extra moving part (a broker) a single-HA-install
setup doesn't need.

### Entities

| Entity | Platform | Notes |
|---|---|---|
| Attic tank level | sensor | 0-100%, from the `2053` reply |
| Mode | sensor | Decoded from `{"tanklevels":{"state": ...}}` |
| R value (unconfirmed) | sensor | Diagnostic; meaning not yet confirmed |
| M value (unconfirmed) | sensor | Diagnostic; meaning not yet confirmed |
| Commissioning drain/mains/rainwater | sensor | Diagnostic, disabled by default; only ever seen once, at commissioning |
| Bus connection | binary_sensor | Connectivity; the one entity that stays available even when the bus is down |
| Drop / No rainwater / Tap / Holiday / Recycle / Engineering mode LED | binary_sensor | From the `1053` push frame |
| Drop / Tap / Holiday / Recycle button | binary_sensor | From the `4033` reply |

### Installation (HACS, custom repository)

1. HACS -> the three-dot menu -> **Custom repositories**.
2. Add `https://github.com/davidanderle/hacs-rainwater-harvesting-uk`,
   category **Integration**.
3. Install **Rain Director**, restart Home Assistant.
4. Settings -> Devices & Services -> **Add Integration** -> search
   "Rain Director".
5. Enter the EW11A's IP address and port (default `8899`). Setup opens
   a connection and waits a few seconds for at least one line of
   traffic to confirm it's the right port before creating the entry.

### Extending it

- New frame type or JSON message: add a decode function in
  `protocol.py` and a case in `BusFrameDecoder`, following the existing
  ones -- it's plain, dependency-free Python with its own test file
  (`tests/test_protocol.py`), so new decoding logic can be validated
  against captured frames without touching Home Assistant at all.
- New confirmed meaning for `r`/`m` or an unmapped `state` code: update
  the tables/docstrings at the top of `protocol.py`.

## Known unknowns

Carried over from the original sniffing notes:

- `r` / `m` in the tank-levels JSON: possibly rainwater/mains fill
  timeout counters, unconfirmed.
- The second byte of the `2053` tank-level payload
  (`tank_level_unknown_byte`): meaning unknown, exposed as a sensor
  attribute in case a pattern emerges.
- The `30xx` device: polled regularly, never replies on this
  installation. Not implemented as an entity since there's nothing to
  show.

## License

MIT -- see `LICENSE`.
