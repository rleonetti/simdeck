"""
Mock SimHub PropertyServer — lets you test the full pipeline without SimHub running.

Listens on TCP port 18082 and behaves like the real PropertyServer plugin,
sending fake RPM telemetry so you can see the LIFX strip respond.

Usage:
    python test_sender.py           # ramps RPM up/down in a loop
    python test_sender.py --redline # holds at redline flash zone

Run this, then run main.py in a separate terminal.
"""

import argparse
import math
import socket
import threading
import time

PORT = 18082
MAX_RPM = 8000.0


def _ramp_rpm(t: float) -> float:
    return ((math.sin(t * math.pi * 2 / 5) + 1) / 2) * MAX_RPM


def handle_client(conn: socket.socket, addr, *, redline: bool) -> None:
    print(f"  Client connected: {addr}")
    try:
        conn.sendall(b"SimHub Property Server\n")

        # Wait for subscribe commands (non-blocking read loop)
        conn.settimeout(0.5)
        buf = ""
        subscribed = set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                chunk = conn.recv(256).decode("utf-8", errors="replace")
                if not chunk:
                    return
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line.startswith("subscribe "):
                        prop = line.split(" ", 1)[1]
                        subscribed.add(prop)
                        # acknowledge with null value
                        conn.sendall(f"Property {prop} double (null)\n".encode())
            except socket.timeout:
                break

        print(f"  Subscribed to: {', '.join(sorted(subscribed)) or '(none)'}")
        print("  Sending telemetry — Ctrl+C to stop.")

        t = 0.0
        while True:
            rpm = MAX_RPM * 0.96 if redline else _ramp_rpm(t)
            pct = int(rpm / MAX_RPM * 100)
            print(f"\r  RPM: {rpm:6.0f} / {MAX_RPM:.0f}  ({pct:3d}%)   ", end="", flush=True)

            updates = [
                f"Property dcp.gd.Rpms double {rpm:.1f}\n",
                f"Property dcp.gd.MaxRpm double {MAX_RPM:.1f}\n",
                f"Property dcp.gd.Gear string 3\n",
                f"Property dcp.gd.SpeedLocal double 120.0\n",
                f"Property dcp.gd.Throttle double 1.0\n",
                f"Property dcp.gd.Brake double 0.0\n",
            ]
            conn.sendall("".join(updates).encode())
            time.sleep(0.05)
            t += 0.05

    except (BrokenPipeError, ConnectionResetError, OSError):
        print("\n  Client disconnected.")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--redline", action="store_true", help="Hold at redline instead of ramping")
    args = parser.parse_args()

    mode = "REDLINE" if args.redline else "RPM RAMP"
    print(f"Mock SimHub PropertyServer — {mode} mode")
    print(f"Listening on TCP port {PORT} — start main.py in another terminal.\n")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", PORT))
        srv.listen(1)
        try:
            while True:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=handle_client, args=(conn, addr), kwargs={"redline": args.redline}, daemon=True
                )
                t.start()
        except KeyboardInterrupt:
            print("\nDone.")
