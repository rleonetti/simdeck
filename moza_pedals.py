"""
Direct Moza pedal input via USB serial (115200 baud).

Reads throttle-output and brake-output at ~100Hz, bypassing SimHub game
telemetry (~15Hz) for a much smoother overlay trace.

Protocol: https://github.com/Lawstorant/boxflat/blob/main/moza-protocol.md
Commands: https://github.com/Lawstorant/boxflat (data/serial.yml)
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_BAUD           = 115200
_START          = 0x7e
_MAGIC          = 13
_DEVICE_PEDALS  = 25    # device-ids.pedals in serial.yml
_GROUP_OUTPUT   = 37    # read group for *-output commands
_CMD_THROTTLE   = 1     # throttle-output id
_CMD_BRAKE      = 2     # brake-output id
_DATA_BYTES     = 2     # bytes per output value
_MAX_VALUE      = 65535 # assumed uint16 range
_PROBE_TIMEOUT  = 0.3   # seconds to wait for probe response
_READ_TIMEOUT   = 0.1   # seconds per read during normal operation
_RETRY_DELAY    = 5.0   # seconds between reconnect attempts


def _checksum(frame: bytearray) -> int:
    return (_MAGIC + sum(frame)) % 256


def _swap_nibbles(b: int) -> int:
    return ((b & 0x0F) << 4) | ((b & 0xF0) >> 4)


def _build_read(device_id: int, group: int, cmd_id: int) -> bytes:
    # length = 1 (cmd_id byte) + _DATA_BYTES (payload, zeros for read)
    length = 1 + _DATA_BYTES
    frame  = bytearray([_START, length, group, device_id, cmd_id] + [0] * _DATA_BYTES)
    frame.append(_checksum(frame))
    return bytes(frame)


_REQ_THROTTLE = _build_read(_DEVICE_PEDALS, _GROUP_OUTPUT, _CMD_THROTTLE)
_REQ_BRAKE    = _build_read(_DEVICE_PEDALS, _GROUP_OUTPUT, _CMD_BRAKE)


class MozaPedals:
    """
    Background thread that reads Moza pedal output values from USB serial.
    throttle and brake are floats 0.0–1.0, updated at ~100 Hz when connected.
    Falls back silently if pyserial is not installed or pedals are not found.
    """

    def __init__(self) -> None:
        self._throttle  = 0.0
        self._brake     = 0.0
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

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="moza-pedals").start()

    def stop(self) -> None:
        self._stop_evt.set()

    # ── internal ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            from serial import Serial, SerialException
        except ImportError:
            logger.warning("MozaPedals: pyserial not installed — Moza input unavailable")
            return

        while not self._stop_evt.is_set():
            port = self._find_port()
            if port is None:
                logger.debug("MozaPedals: no pedals found, retrying in %.0fs", _RETRY_DELAY)
                self._stop_evt.wait(_RETRY_DELAY)
                continue

            logger.info("MozaPedals: connecting on %s", port)
            try:
                with Serial(port, baudrate=_BAUD, timeout=_READ_TIMEOUT) as ser:
                    ser.reset_input_buffer()
                    self._connected = True
                    self._port = port
                    logger.info("MozaPedals: connected on %s", port)

                    while not self._stop_evt.is_set():
                        t = self._request(ser, _REQ_THROTTLE, _CMD_THROTTLE)
                        b = self._request(ser, _REQ_BRAKE,    _CMD_BRAKE)
                        if t is not None and b is not None:
                            with self._lock:
                                self._throttle = min(1.0, t / _MAX_VALUE)
                                self._brake    = min(1.0, b / _MAX_VALUE)
            except SerialException as exc:
                logger.warning("MozaPedals: connection lost (%s)", exc)
            finally:
                self._connected = False
                self._port = None

    def _find_port(self) -> str | None:
        try:
            from serial.tools.list_ports import comports
            from serial import Serial
        except ImportError:
            return None

        for info in comports():
            desc = (info.description  or "").lower()
            mfr  = (info.manufacturer or "").lower()
            if not ("silicon labs" in desc or "silicon labs" in mfr
                    or "cp210"     in desc or "moza"         in desc):
                continue
            try:
                with Serial(info.device, baudrate=_BAUD, timeout=_PROBE_TIMEOUT) as ser:
                    ser.reset_input_buffer()
                    ser.write(_REQ_THROTTLE)
                    if self._read_one(ser, _CMD_THROTTLE) is not None:
                        return info.device
            except Exception:
                pass
        return None

    def _request(self, ser, req: bytes, expected_cmd: int) -> int | None:
        try:
            ser.write(req)
            return self._read_one(ser, expected_cmd)
        except Exception:
            return None

    def _read_one(self, ser, expected_cmd: int) -> int | None:
        # Scan for start byte (clears any stale data including unchread checksums)
        for _ in range(64):
            b = ser.read(1)
            if not b:
                return None
            if b[0] == _START:
                break
        else:
            return None

        lb = ser.read(1)
        if not lb:
            return None
        length = lb[0]
        if not (2 <= length <= 11):
            return None

        # Read: group(1) + device_id(1) + cmd_id(1) + data(length-1) + checksum(1)
        rest = ser.read(length + 3)
        if len(rest) < length + 3:
            return None

        resp_group = rest[0] ^ 0x80  # un-toggle MSB to recover original group
        cmd_byte   = rest[2]
        data       = rest[3: 3 + _DATA_BYTES]

        if resp_group != _GROUP_OUTPUT or cmd_byte != expected_cmd:
            return None
        if len(data) < _DATA_BYTES:
            return None

        return int.from_bytes(data, "big")
