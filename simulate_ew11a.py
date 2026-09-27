#!/usr/bin/env python3
"""Fake EW11A bridge for testing the Rain Director HA integration
without the real hardware.

Listens on TCP and streams synthetic-but-valid bus traffic in the same
frame format the real bridge uses (see protocol.py / traffic_decoder.py):
same checksum, same poll/reply pairing, same debug JSON line, frames
terminated with a bare '\\r' like the real bridge does. Good enough to
get the config flow's "wait for real traffic" check to pass and to see
entities actually move in Home Assistant afterwards.

Usage:
    python3 simulate_ew11a.py                  # listens on 0.0.0.0:8899
    python3 simulate_ew11a.py --port 8899

Then, in Home Assistant's "Add Integration" flow, use this Mac's LAN
IP (e.g. 192.168.1.46) as the host -- not 0.0.0.0, that's only a bind
address, not something reachable from another device.

Note: your Mac's firewall may prompt to allow incoming connections for
Python the first time HA connects (System Settings -> Network ->
Firewall). Allow it, or the connection will just hang/refuse.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import threading
import time

CHECKSUM_SEED = 0x3C


def checksum(payload: str) -> str:
    cs = CHECKSUM_SEED
    for ch in payload:
        cs ^= ord(ch)
    return f"{cs:02x}"


def frame(payload: str) -> str:
    """Build a full '<...' frame line (checksum + trailing \\r) from a payload."""
    return "<" + payload + checksum(payload) + "\r"


def json_line(**tanklevels: str) -> str:
    return json.dumps({"tanklevels": tanklevels}) + "\r"


def handle_client(conn: socket.socket, addr: tuple) -> None:
    print(f"[+] Client connected: {addr}")
    tank_level = 40
    mode_state = 1
    led_on = False
    button_held = False

    def send(text: str) -> None:
        conn.sendall(text.encode("ascii"))

    try:
        tick = 0
        while True:
            tick += 1

            # tank level: poll, then reply -- drifts randomly so the
            # sensor visibly moves in HA
            send("<" + "20123d" + "\r")
            time.sleep(0.05)
            tank_level = max(0, min(100, tank_level + random.choice([-1, -1, 0, 0, 0, 1, 1])))
            send(frame(f"2053{tank_level:02x}00"))

            # the permanently-absent third device: polled, never replies
            send("<" + "30123c" + "\r")

            # buttons: mostly released, briefly "held" every ~15 ticks
            send("<" + "40123b" + "\r")
            time.sleep(0.05)
            button_held = (tick % 15 == 0) or (button_held and tick % 15 == 1)
            send(frame("4033" + ("01" if button_held else "00")))

            # LED push: unsolicited, toggles occasionally
            if tick % 8 == 0:
                led_on = not led_on
                send(frame("1053" + ("0100" if led_on else "0000")))

            # debug JSON: mode/state line, resent periodically
            if tick % 10 == 0:
                mode_state = random.choice([0, 1, 1, 1, 5])  # mostly idle
                send(json_line(top="0", bottom="0", state=str(mode_state), r="58", m="21"))

            time.sleep(1)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        conn.close()
        print(f"[-] Client disconnected: {addr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8899, help="bind port (default: 8899)")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(5)
    print(f"Fake EW11A listening on {args.host}:{args.port}")
    print("Point Home Assistant's config flow at this Mac's LAN IP and this port.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.close()


if __name__ == "__main__":
    main()