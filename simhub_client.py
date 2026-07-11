import logging
import socket
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# SimHub property name → our telemetry key
PROPERTIES = {
    "dcp.gd.Rpms":              "rpm",
    "dcp.gd.MaxRpm":            "max_rpm",
    "dcp.gd.Gear":              "gear",
    "dcp.gd.SpeedLocal":        "speed",
    "dcp.gd.Throttle":          "throttle",
    "dcp.gd.Brake":             "brake",
    # Flags
    "dcp.gd.Flag_Yellow":       "flag_yellow",
    "dcp.gd.Flag_Red":          "flag_red",
    "dcp.gd.Flag_Blue":         "flag_blue",
    "dcp.gd.Flag_White":        "flag_white",
    "dcp.gd.Flag_Checkered":    "flag_checkered",
    "dcp.gd.Flag_Black":        "flag_black",
    # Pit limiter
    "dcp.gd.PitLimiterActive":  "pit_limiter",
    # Lap timing (timespans arrive as "HH:MM:SS.fff", parsed to seconds)
    "dcp.gd.CurrentLap":        "current_lap",
    "dcp.gd.CurrentLapTime":    "current_lap_time",
    "dcp.gd.LastLapTime":       "last_lap_time",
    "dcp.gd.BestLapTime":       "best_lap_time",
    # Lap validity (0 = invalid / track limits, 1 = valid; absent = assume valid)
    "dcp.gd.IsLapValid":        "is_lap_valid",
    # Session meta (strings — arrive once per session)
    "dcp.gd.VehicleName":       "vehicle",
    "dcp.gd.TrackName":         "track",
}


def _parse_timespan(s: str) -> float:
    """Parse SimHub timespan strings (HH:MM:SS.fff or MM:SS.fff) to seconds."""
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(s)
    except ValueError:
        return 0.0


class SimHubClient:
    """
    Receives telemetry from SimHub via the SimHubPropertyServer plugin (TCP).

    Install the plugin:
      1. Download PropertyServer.dll from
         https://github.com/pre-martin/SimHubPropertyServer/releases
      2. Copy it to your SimHub installation folder (e.g. C:\\Program Files (x86)\\SimHub)
      3. In SimHub: Settings → Plugins → enable "SimHub Property Server"
      4. Restart SimHub

    The client auto-reconnects if SimHub restarts.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18082):
        self.host = host
        self.port = port
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._data: dict = {}
        self._last_received: float = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="simhub-tcp")
        self._thread.start()
        logger.info("SimHub TCP client started — connecting to %s:%d", self.host, self.port)

    def _connect(self) -> Optional[socket.socket]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.host, self.port))
            banner = sock.makefile().readline().strip()
            logger.success("Connected to: %s", banner)
            for prop in PROPERTIES:
                sock.sendall(f"subscribe {prop}\n".encode())
            sock.settimeout(2.0)
            return sock
        except (OSError, ConnectionRefusedError) as exc:
            logger.debug("SimHub connect failed: %s — retrying in 3s", exc)
            return None

    def _loop(self) -> None:
        buf = ""
        sock: Optional[socket.socket] = None

        while self._running:
            if sock is None:
                sock = self._connect()
                if sock is None:
                    time.sleep(3.0)
                    continue
                buf = ""

            try:
                chunk = sock.recv(4096)
                if not chunk:
                    logger.info("SimHub disconnected — reconnecting...")
                    sock.close()
                    sock = None
                    continue
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    self._handle_line(line.strip())
            except socket.timeout:
                continue
            except (OSError, ConnectionResetError) as exc:
                logger.debug("SimHub socket error: %s", exc)
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None

        if sock:
            try:
                sock.sendall(b"disconnect\n")
                sock.close()
            except Exception:
                pass

    def _handle_line(self, line: str) -> None:
        # Protocol: "Property <name> <type> <value>"
        if not line.startswith("Property "):
            return
        parts = line.split(" ", 3)
        if len(parts) < 4:
            return
        _, prop_name, type_str, value_str = parts
        key = PROPERTIES.get(prop_name)
        if not key:
            return
        if value_str == "(null)":
            return
        try:
            if type_str in ("double", "integer"):
                value: float | str = float(value_str)
            elif type_str == "timespan":
                value = _parse_timespan(value_str)
            else:
                value = value_str
        except ValueError:
            return
        with self._lock:
            self._data[key] = value
            self._last_received = time.monotonic()

    def get_data(self) -> dict:
        with self._lock:
            return dict(self._data)

    def seconds_since_last_packet(self) -> float:
        with self._lock:
            if self._last_received == 0.0:
                return float("inf")
            return time.monotonic() - self._last_received

    def stop(self) -> None:
        self._running = False
