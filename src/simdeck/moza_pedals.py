"""
Direct Moza pedal input via USB HID.

Reads throttle and brake axes from the R12 Base HID joystick at ~100 Hz,
bypassing SimHub game telemetry for a much smoother overlay trace.

Device: Gudsen MOZA R12 Base (VID=0x346E, PID=0x0016)
Axis layout confirmed (report ID 1, all 16-bit LE signed):
  bytes 5-6   = Z   axis = Throttle
  bytes 11-12 = Ry  axis = Brake
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_VID          = 0x346E   # Gudsen Technology (Moza Racing)
_PID          = 0x0016   # R12 Base
_REPORT_SIZE  = 64
_THROTTLE_OFF = 5        # byte offset in HID report (Z axis)
_BRAKE_OFF    = 11       # byte offset in HID report (Ry axis)
_CLUTCH_OFF   = 15       # byte offset in HID report (Dial axis)
_RETRY_DELAY  = 5.0      # seconds between reconnect attempts


def _axis_to_float(data: bytes, offset: int) -> float:
    """Convert 16-bit LE signed axis value (-32768..32767) to 0.0..1.0."""
    raw = int.from_bytes(data[offset:offset + 2], "little", signed=True)
    return (raw + 32768) / 65535.0


class MozaPedals:
    """
    Background thread that reads Moza pedal axes from the R12 Base HID device.
    throttle and brake are floats 0.0–1.0, updated at ~100 Hz when connected.
    Falls back silently if hidapi is not installed or the device is not found.
    """

    def __init__(self) -> None:
        self._throttle  = 0.0
        self._brake     = 0.0
        self._clutch    = 0.0
        self._lock      = threading.Lock()
        self._stop_evt  = threading.Event()
        self._connected = False
        self._port: str | None = None

    # ── public ───────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str | None:
        return self._port

    @property
    def throttle(self) -> float:
        with self._lock:
            return self._throttle

    @property
    def brake(self) -> float:
        with self._lock:
            return self._brake

    @property
    def clutch(self) -> float:
        with self._lock:
            return self._clutch

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="moza-pedals").start()

    def stop(self) -> None:
        self._stop_evt.set()

    # ── internal ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import hid
        except ImportError:
            logger.warning("MozaPedals: hidapi not installed — Moza input unavailable")
            return

        while not self._stop_evt.is_set():
            dev = hid.device()
            try:
                dev.open(_VID, _PID)
                dev.set_nonblocking(0)
                self._connected = True
                self._port = f"{dev.get_manufacturer_string()} {dev.get_product_string()}"
                logger.info("MozaPedals: connected to %s", self._port)

                while not self._stop_evt.is_set():
                    data = bytes(dev.read(_REPORT_SIZE, timeout_ms=50))
                    if not data or data[0] != 0x01:
                        continue
                    with self._lock:
                        self._throttle = _axis_to_float(data, _THROTTLE_OFF)
                        self._brake    = _axis_to_float(data, _BRAKE_OFF)
                        self._clutch   = _axis_to_float(data, _CLUTCH_OFF)

            except Exception as exc:
                logger.warning("MozaPedals: disconnected (%s)", exc)
            finally:
                self._connected = False
                self._port = None
                try:
                    dev.close()
                except Exception:
                    pass

            self._stop_evt.wait(_RETRY_DELAY)
