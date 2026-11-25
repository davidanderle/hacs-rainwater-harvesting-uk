# For developers
- `traffic_decoder.py` -- standalone sniffer/decoder script, run against the raw bus stream from the command line. Useful for debugging or for capturing new frame types before wiring them into the integration.
- `custom_components/rainwater_harvesting/` -- the HACS integration itself.
- `tests/test_protocol.py` -- unit tests for the integration's decoder.

## Standalone: sniffing from the command line

```
nc 192.168.1.46 8899 | python3 traffic_decoder.py
```

This prints a human-readable, change-filtered log of bus traffic -- handy for debugging, or for capturing an unfamiliar frame before adding a decoder for it. See the module docstring in `traffic_decoder.py` for the full frame format writeup (checksum, frame types, the JSON debug messages interleaved on the same line).

## Home Assistant integration

The EW11A only being reachable over raw TCP -- no MODBUS, nothing pollable, no request/response protocol on a schedule -- ruled out both a polling `DataUpdateCoordinator` and needing an MQTT broker in the middle. Instead, the integration opens **one persistent TCP connection**
to the EW11A for as long as it's loaded and reads continuously, exactly like the `nc` command above. That connection feeds a decoder ported from `traffic_decoder.py` (checksum validation, frame parsing, JSON handling, poll/reply pairing), which produces immutable state snapshots; an entity's state only changes when a decoded value actually differs from last time. The bus itself does roughly 100 messages/sec, but the vast majority are re-announcements of state that hasn't moved, so this filtering is what keeps Home Assistant's recorder database from filling up with identical rows.

If the connection drops (WiFi blip, EW11A reboot), it reconnects with exponential backoff (5s -> 10s -> 20s -> ... capped at 60s).

> [!Note]
> the EW11A accepts at least two simultaneous TCP connections in this mode, so you can run `nc` (or `traffic_decoder.py`) alongside the integration to watch the bus while it runs.



### Entities

| Entity | Platform | Notes |
|---|---|---|
| Attic tank level | sensor | 0-100%, from the `2053` reply |
| Mode | sensor | Decoded from `{"tanklevels":{"state": ...}}` |
| Display | sensor | Screen shown on the control unit (`mains_only`, `normal`, `holiday`, `refresh`), from the `(display)` log lines. **Latched** |
| Last action | sensor | Last announced action (`fill_from_mains`, `fill_from_rainwater`, `holiday_drain_tank`, `refresh_tank`, `flush_inlet`, ...). **Latched** |
| Unknown messages | sensor | Diagnostic; counts frames, JSON or text lines no decoder recognises. `last_message` holds the latest and `context` the raw bus lines (up to 12, oldest first) that came just before it; the same lines are appended to the warning in the log |
| Rainwater refill count / Mains refill count | sensor | Diagnostic counters (`r` / `m` in the tank-levels JSON): how many times a rainwater / mains refill has been triggered. `total_increasing`; restart from 0 when the unit is re-commissioned |
| Commissioning drain/mains/rainwater | sensor | Diagnostic, disabled by default. Sent by the unit once, at power-up / commissioning. **Latched** |
| Bus connection | binary_sensor | Connectivity; the one entity that stays available even when the bus is down |
| Drop / No rainwater / Tap / Holiday / Recycle / Engineering mode LED | binary_sensor | From the `1053` push frame |
| Rain filling / Mains filling LED | binary_sensor | Low byte of the `1053` frame (`0x01` / `0x02`). Lit while the tank is being filled from rainwater / the mains (confirmed on the unit during real refills) |
| Rainwater saved | sensor | Litres, net: rain filled minus rain drained. Can go down; see *Water counters* |
| Rainwater filled / Mains water filled | sensor | Litres delivered into the tank by each source (always increasing) |
| Rainwater drained | sensor | Diagnostic: litres drained back out in holiday/refresh cycles |
| Drop / Tap / Holiday / Recycle button | binary_sensor | From the `4033` reply |

**Latched values.** The unit sends its commissioning data only once (at power-up) and its display/action lines only when something happens, and never repeats them. So *Display*, *Last action* and the three *Commissioning* sensors keep their **last known value**: it is saved to `.storage` as soon as it changes and restored when Home Assistant restarts, and these sensors stay available while the bus is down instead of flipping to unavailable. They read *unknown* only if the unit has never sent the value since the integration started saving it (power-cycle the unit once to make it send the commissioning data again). Everything else (level, mode, `r`, `m`, LEDs, buttons) is live and goes unavailable while the bus is down.

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

- Exactly what `r` / `m` count. Two weeks of real use show they are counters of how many times a rainwater / mains refill was triggered, and the commissioning steps count too (a run ends at `r=1,m=2`, and both start again from 0 on re-commissioning). During commissioning `m` was already 1 before any mains-filling light had come on, so they may count more than refills. They are not derived from the stored commissioning values (two runs with different values followed the identical path). Two checks need no code, only Home Assistant's history: (1) over any period, does the change in each counter equal the number of times the matching *Rain filling* / *Mains filling* LED switched on? (2) *Rainwater filled* (or *Mains water filled*) litres divided by the change in its counter is the average litres per refill: roughly constant means the unit refills in fixed steps; widely varying means it counts triggers that don't all complete.
- The second byte of the `2053` tank-level payload (`tank_level_unknown_byte`): meaning unknown, exposed as a sensor attribute in case a pattern emerges.
- LED bits `0x40` and `0x80` in the high byte of the `1053` frame. Seen only during commissioning, identically in every run: `0xC000` from the start of the drain, `0x4001` from "Level sensor auto commission" (rain filling), `0x4002` from "Auto commission - mains fill", then `0x0100` (Drop LED) at completion. Left **unmapped on purpose**: the warning is the instrument for settling it. Two readings fit all the data: independent flags (`0x40` = commissioning in progress, `0x80` = draining) or `0x40` = "filling" with `0xC0` a 2-bit drain code. They differ on two things you'll see anyway: a normal top-up (flags reading: no `0x40`) and a holiday/refresh drain outside commissioning (flags reading: `0x80` alone). If `0x80` turns out to be "draining" it is a direct, immediate signal that could replace the unconfirmed `DRAIN_MODES` state codes in the water accounting.
- The `commisiondata` values (`drain`, `mains`, `rainwater`). Observed runs: `0 / 73 / 100`, `0 / 228 / 105`, `0 / 243 / 105`. `drain` was always 0 although the drain took ~4 minutes, so it is not a timed drain. In the last run the 10% rainwater step (59% to 69%) took exactly 21 s and `rainwater` was 105 (5.0 per second); the mains step does not fit the same unit (10% took 154 s for 243). `rainwater` is stable across runs while `mains` varies about 3x, so `mains` looks sensitive to supply conditions. Unconfirmed; more runs with full level lines are needed. Since `r`/`m` are counts, these values are a different kind of quantity. One idea to test: they are the unit's own time-outs for supervising a refill (the manual lists a "slow fill" commissioning time-out). Cutting the rainwater supply for one refill and timing how long the unit takes to light *No rainwater* / fall back to the mains would give a direct number to compare with `rainwater`.
- Which operating states are *drains* (`DRAIN_MODES = {3, 5}` in `water.py`, from the original state naming): not independently confirmed. After a holiday or refresh drain, **Rainwater drained** should rise by about one tank; if it doesn't, the state codes need adjusting.
- The `30xx` device: polled regularly (`<30123c`), never replies on this installation. Not implemented as an entity since there's nothing to show. **Seen once doing something else, see the next item.**
- `Flush inlet` and the frame `<30153b`. Observed together while the house was empty, about 32 h after the last refill; the Rain filling LED came on during that refill and the Recycle LED stayed off. `Flush inlet` is recorded as the action `flush_inlet` (the unit's own wording only; what it physically does is unknown). The frame is valid (checksum OK): type `30`, code `15`, no payload, the same shape as the poll `<30123c` with `15` where polls have `12`. Two readings: a command from the controller to the type-30 device, or a reply from it to the regular poll. It is **left flagged on purpose**, because the neighbouring lines settle it: if it directly follows `<30123c` it occupies that poll's reply slot. Every unknown message now carries the raw lines that preceded it (log warning and the `context` attribute), so the next occurrence answers that by itself. The manufacturer documents an automatic refresh after 72 h (3 days) of inactivity, and its panel lists a *Flush through* light (presumably the Recycle light); neither fits an event at under 32 h with that light off, so this looks like a different, lighter routine than the documented refresh. Its trigger and its effect on the tank level are both unconfirmed. To capture next time from Home Assistant's history around the event: the tank level (did it fall before it rose?), *Mode* and its `mode_code` (a state code not in `STATE_NAMES` shows as `unknown_N`), *Display*, *Last action*, the Rain/Mains filling LEDs (on and off times), *Rainwater refill count* and *Mains refill count* (did `r` or `m` move?), and the water counters (*Rainwater filled* and *Rainwater drained* deltas). The last two matter because a flush that drains in a state outside `DRAIN_MODES` would make the water counters over-count.