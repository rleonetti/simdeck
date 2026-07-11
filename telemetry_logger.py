"""
Telemetry logger — records lap times to SQLite.

The caller drives everything by calling feed(telemetry) on each poll tick.
Lap detection fires when current_lap increments and last_lap_time is valid.
"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

_DB = Path.home() / "Documents" / "SimDeck" / "telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    game        TEXT
);
CREATE TABLE IF NOT EXISTS laps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    lap_number  INTEGER,
    lap_time_ms INTEGER NOT NULL,
    recorded_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS laps_session ON laps(session_id);
"""

_MIN_VALID_LAP_MS = 10_000   # ignore sub-10-second "laps" (outlap artefacts)


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


class TelemetryLogger:
    """Thread-safe lap time logger backed by SQLite."""

    def __init__(self) -> None:
        _DB.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock     = threading.Lock()
        self._session_id: int | None = None
        self._prev_lap: float | None = None
        self._prev_last_ms: int | None = None
        self.on_lap_recorded: Callable[[], None] | None = None

    # ── public ────────────────────────────────────────────────────────────────

    @property
    def recording(self) -> bool:
        return self._session_id is not None

    def start_session(self, game: str | None = None) -> None:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_at, game) VALUES (?, ?)",
                (datetime.now().isoformat(timespec="seconds"), game),
            )
            self._conn.commit()
            self._session_id  = cur.lastrowid
            self._prev_lap    = None
            self._prev_last_ms = None

    def stop_session(self) -> None:
        with self._lock:
            self._session_id  = None
            self._prev_lap    = None
            self._prev_last_ms = None

    def feed(self, telemetry: dict) -> None:
        """Call with the latest telemetry dict. Detects and stores lap completions."""
        if self._session_id is None:
            return
        cur_lap    = telemetry.get("current_lap")
        last_secs  = telemetry.get("last_lap_time", 0.0)
        if cur_lap is None:
            return

        last_ms = _ms(last_secs) if last_secs else 0

        prev_lap = self._prev_lap
        self._prev_lap = cur_lap

        # Lap completed: lap counter increased and we have a valid new time
        if (
            prev_lap is not None
            and cur_lap > prev_lap
            and last_ms >= _MIN_VALID_LAP_MS
            and last_ms != self._prev_last_ms
        ):
            lap_number = int(prev_lap)
            self._prev_last_ms = last_ms
            with self._lock:
                self._conn.execute(
                    "INSERT INTO laps (session_id, lap_number, lap_time_ms, recorded_at)"
                    " VALUES (?, ?, ?, ?)",
                    (self._session_id, lap_number, last_ms,
                     datetime.now().isoformat(timespec="seconds")),
                )
                self._conn.commit()
            if self.on_lap_recorded:
                self.on_lap_recorded()

    # ── queries ───────────────────────────────────────────────────────────────

    def current_session_laps(self) -> list[tuple[int, int]]:
        """Return [(lap_number, lap_time_ms), ...] for the current session."""
        if self._session_id is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT lap_number, lap_time_ms FROM laps"
                " WHERE session_id = ? ORDER BY lap_number",
                (self._session_id,),
            ).fetchall()
        return rows

    def session_history(self, limit: int = 50) -> list[tuple]:
        """Return summary rows for the most recent sessions.
        Columns: (id, started_at, game, lap_count, best_ms, avg_ms)
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id, s.started_at, s.game,
                       COUNT(l.id)        AS lap_count,
                       MIN(l.lap_time_ms) AS best_ms,
                       CAST(AVG(l.lap_time_ms) AS INTEGER) AS avg_ms
                FROM sessions s
                LEFT JOIN laps l ON l.session_id = s.id
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return rows

    def session_laps(self, session_id: int) -> list[tuple[int, int]]:
        """Return laps for any session by id."""
        with self._lock:
            return self._conn.execute(
                "SELECT lap_number, lap_time_ms FROM laps"
                " WHERE session_id = ? ORDER BY lap_number",
                (session_id,),
            ).fetchall()
