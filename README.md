[![hassfest](https://github.com/davidanderle/hacs-rainwater-harvesting-uk/actions/workflows/hassfest.yml/badge.svg)](https://github.com/davidanderle/hacs-rainwater-harvesting-uk/actions/workflows/hassfest.yml)
[![Tests](https://github.com/davidanderle/hacs-rainwater-harvesting-uk/actions/workflows/tests.yml/badge.svg)](https://github.com/davidanderle/hacs-rainwater-harvesting-uk/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

# RainWater Harvesting LTD Rain Director — RS485 Protocol & Home Assistant Integration

Fully reverse-engineered RS485 protocol for the [Rain Director](https://www.rainwaterharvesting.co.uk/product/rain-director/)
control unit, plus a HACS integration built on top of it. No public protocol documentation or Home Assistant integration existed prior to this work.

This project is an independent Home Assistant integration and is not affiliated with, endorsed by, or sponsored by RainWater Harvesting LTD. RainWater Harvesting LTD and the RainWater Harvesting LTD logo are trademarks of their respective owners.

No control is implemented -- this is read-only monitoring, matching what's actually feasible on this installation today.

## Contents

- `traffic_decoder.py` -- standalone sniffer/decoder script, run against the raw bus stream from the command line. Useful for debugging or for capturing new frame types before wiring them into the integration.
- `custom_components/rainwater_harvesting/` -- the HACS integration itself.
- `tests/test_protocol.py` -- unit tests for the integration's decoder.

## The hardware setup

An Elfin EW11A RS485-to-WiFi bridge is wired onto the Rain Director's bus (valve controller <-> LED controller <-> button controller <-> attic tank level sensor), configured in TCP-server mode. That exposes the bus as a raw TCP stream: anything that opens a TCP connection to it sees every message on the wire, unencrypted.

### Standalone: sniffing from the command line

```
nc 192.168.1.46 8899 | python3 traffic_decoder.py
```

This prints a human-readable, change-filtered log of bus traffic -- handy for debugging, or for capturing an unfamiliar frame before adding a decoder for it. See the module docstring in `traffic_decoder.py` for the full frame format writeup (checksum, frame types, the JSON debug messages interleaved on the same line).

## Home Assistant integration

The EW11A only being reachable over raw TCP -- no MODBUS, nothing pollable, no request/response protocol on a schedule -- ruled out both a polling `DataUpdateCoordinator` and needing an MQTT broker in the middle. Instead, the integration opens **one persistent TCP connection**
to the EW11A for as long as it's loaded and reads continuously, exactly like the `nc` command above. That connection feeds a decoder ported from `traffic_decoder.py` (checksum validation, frame parsing, JSON handling, poll/reply pairing), which produces immutable state snapshots; an entity's state only changes when a decoded value actually differs from last time. The bus itself does roughly 100 messages/sec, but the vast majority are re-announcements of state that hasn't moved, so this filtering is what keeps Home Assistant's recorder database from filling up with identical rows.

If the connection drops (WiFi blip, EW11A reboot), it reconnects with exponential backoff (5s -> 10s -> 20s -> ... capped at 60s).

**Note:** the EW11A accepts at least two simultaneous TCP connections in this mode, so you can run `nc` (or `traffic_decoder.py`) alongside the integration to watch the bus while it runs.

### Entities

| Entity | Platform | Notes |
|---|---|---|
| Attic tank level | sensor | 0-100%, from the `2053` reply |
| Mode | sensor | Decoded from `{"tanklevels":{"state": ...}}` |
| Display | sensor | Screen shown on the control unit (`mains_only`, `normal`, `holiday`, `refresh`), from the `(display)` log lines |
| Last action | sensor | Last announced action (`fill_from_mains`, `fill_from_rainwater`, `holiday_drain_tank`, `refresh_tank`) |
| Unknown messages | sensor | Diagnostic; counts frames, JSON or text lines no decoder recognises. `last_message` attribute holds the latest |
| R value (unconfirmed) | sensor | Diagnostic; meaning not yet confirmed |
| M value (unconfirmed) | sensor | Diagnostic; meaning not yet confirmed |
| Commissioning drain/mains/rainwater | sensor | Diagnostic, disabled by default; only ever seen once, at commissioning |
| Bus connection | binary_sensor | Connectivity; the one entity that stays available even when the bus is down |
| Drop / No rainwater / Tap / Holiday / Recycle / Engineering mode LED | binary_sensor | From the `1053` push frame |
| Rain filling / Mains filling LED | binary_sensor | Low byte of the `1053` frame (`0x01` / `0x02`). Lit while the tank is being filled from rainwater / the mains (confirmed on the unit during real refills) |
| Rainwater saved | sensor | Litres, net: rain filled minus rain drained. Can go down; see *Water counters* |
| Rainwater filled / Mains water filled | sensor | Litres delivered into the tank by each source (always increasing) |
| Rainwater drained | sensor | Diagnostic: litres drained back out in holiday/refresh cycles |
| Drop / Tap / Holiday / Recycle button | binary_sensor | From the `4033` reply |

### Installation (HACS, custom repository)

1. HACS -> the three-dot menu -> **Custom repositories**.
2. Add `https://github.com/davidanderle/hacs-rainwater-harvesting-uk`,
   category **Integration**.
3. Install **Rainwater Harvesting**, restart Home Assistant.
4. Settings -> Devices & Services -> **Add Integration** -> search "RainWater Harvesting".
5. Enter the EW11A's IP address and port (default `8899`) and your loft tank's volume in litres (default `100`). Setup opens a connection and waits a few seconds for at least one line of traffic to confirm it's the right port before creating the entry.

To change the IP address, port or tank volume later (e.g. after replacing the EW11A): Settings -> Devices & Services -> Rain Director -> three-dot menu -> **Reconfigure**. Entities, history and water totals are kept. The bridge is only test-connected if the host or port changed, so the tank volume can still be edited while the bridge is down. Entries created before the tank volume option existed use the default until you set it.

### Extending it

- New frame type or JSON message: add a decode function in `protocol.py` and a case in `BusFrameDecoder`, following the existing ones -- it's plain, dependency-free Python with its own test file (`tests/test_protocol.py`), so new decoding logic can be validated against captured frames without touching Home Assistant at all.
- New confirmed meaning for `r`/`m` or an unmapped `state` code: update the tables/docstrings at the top of `protocol.py`.

## Water counters

The integration turns changes in the tank level into litres (using the tank volume you configured: 1% = 1 L for 100 L) and attributes them with the unit's own filling lights. Four sensors, all kept across restarts and reloads in their own store (`.storage/rainwater_harvesting.<entry id>.water`):

| Sensor | Meaning |
|---|---|
| **Rainwater filled** | Litres pushed into the tank while the *Rain filling* light is on. Only increases. |
| **Mains water filled** | Litres pushed into the tank while the *Mains filling* light is on. Only increases. |
| **Rainwater drained** | Litres the unit emptied back out in a holiday or refresh drain. Only increases. Diagnostic. |
| **Rainwater saved** | *Rainwater filled* minus *Rainwater drained*. |

*Rainwater saved* is the rainwater delivered into the tank, i.e. mains water that did not have to be used. A refresh or holiday cycle drains the tank and later pushes fresh rainwater back in, and that refill is not a saving, so the drain is subtracted and the refill adds it back: a full cycle nets to about zero. Because it can go down, it is a `total` sensor (not `total_increasing`, which Home Assistant would read as a meter reset). Use it with the Energy dashboard or `utility_meter` for daily/monthly figures.

What is counted: only level changes in normal operation. Commissioning (states 9, 15) and engineering mode (13) are ignored entirely; falls in normal use are the house drawing water and are never counted; if both filling lights are on at once the source is ambiguous and the rise is skipped. After a bus reconnect the first reading only sets a new baseline, so a reconnect can never look like a fill.

Limits, all small: the level moves in 1% steps; water drawn by the house *during* a fill hides part of that fill (only the net rise is visible); the unit reports its mode about every 7 s, so the first/last few percent of a drain can be missed; and a drain is assumed to be rainwater, so if mains backup water was in the tank at the time *Rainwater saved* dips slightly too low. Deliberately **not** done: attributing consumption to a source, which would need a model of what is mixed in the tank and would often be wrong.

Changing the tank volume only affects water counted from then on; totals already recorded are not rewritten.

## Known unknowns

Carried over from the original sniffing notes:

- `r` / `m` in the tank-levels JSON: possibly rainwater/mains fill timeout counters, unconfirmed.
- The second byte of the `2053` tank-level payload (`tank_level_unknown_byte`): meaning unknown, exposed as a sensor attribute in case a pattern emerges.
- LED bits `0x40` and `0x80` in the high byte of the `1053` frame: only seen during commissioning (`0xC000` while draining, `0x40` during the fills). Reported as unmapped bits.
- Which operating states are *drains* (`DRAIN_MODES = {3, 5}` in `water.py`, from the original state naming): not independently confirmed. After a holiday or refresh drain, **Rainwater drained** should rise by about one tank; if it doesn't, the state codes need adjusting.
- The `30xx` device: polled regularly, never replies on this installation. Not implemented as an entity since there's nothing to show.

## License

MIT -- see `LICENSE`.