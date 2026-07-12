"""
Telemetry logger — records lap times to SQLite.

The caller drives everything by calling feed(telemetry) on each poll tick.
Lap detection fires when current_lap increments and last_lap_time is valid.
"""
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

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
    valid       INTEGER NOT NULL DEFAULT 1,
    recorded_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS laps_session ON laps(session_id);
"""

_MIGRATIONS = [
    "ALTER TABLE laps ADD COLUMN valid INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE sessions ADD COLUMN vehicle TEXT",
    "ALTER TABLE sessions ADD COLUMN track TEXT",
]

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
        self._run_migrations()
        self._lock              = threading.Lock()
        self._session_id: int | None = None
        self._prev_lap: float | None = None
        self._prev_last_ms: int | None = None
        self._lap_ever_invalid  = False
        self._prev_checkered    = False
        self._session_vehicle: str | None = None
        self._session_track:   str | None = None
        self.on_lap_recorded: Callable[[], None] | None = None

    def _run_migrations(self) -> None:
        for sql in _MIGRATIONS:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

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
            self._session_id   = None
            self._prev_lap     = None
            self._prev_last_ms = None
            self._prev_checkered   = False
            self._session_vehicle  = None
            self._session_track    = None

    def delete_session(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM laps WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def _update_session_meta(self, vehicle: str | None, track: str | None) -> None:
        """Write vehicle/track to the session row the first time they appear."""
        if self._session_id is None:
            return
        changed = (vehicle and vehicle != self._session_vehicle) or \
                  (track   and track   != self._session_track)
        if not changed:
            return
        if vehicle:
            self._session_vehicle = vehicle
        if track:
            self._session_track = track
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET vehicle=?, track=? WHERE id=?",
                (self._session_vehicle, self._session_track, self._session_id),
            )
            self._conn.commit()

    def feed(self, telemetry: dict) -> None:
        """Call with the latest telemetry dict. Detects and stores lap completions."""
        if self._session_id is None:
            return
        self._update_session_meta(
            telemetry.get("vehicle") or None,
            telemetry.get("track")   or None,
        )
        cur_lap   = telemetry.get("current_lap")
        last_secs = telemetry.get("last_lap_time", 0.0)
        if cur_lap is None:
            return

        last_ms   = _ms(last_secs) if last_secs else 0
        is_valid  = bool(telemetry.get("is_lap_valid", 1))

        checkered      = bool(telemetry.get("flag_checkered", 0))
        checkered_edge = checkered and not self._prev_checkered
        self._prev_checkered = checkered


        # Accumulate: if validity ever drops during this lap, the whole lap is tainted.
        if not is_valid:
            self._lap_ever_invalid = True

        # First feed after recording starts: backfill the most recently completed
        # lap if one is already available (handles auto-record starting mid-session).
        # We can't know its validity retroactively, so we assume valid.
        if self._prev_lap is None:
            self._prev_lap = cur_lap
            if last_ms >= _MIN_VALID_LAP_MS:
                lap_number = max(1, int(cur_lap) - 1)
                self._prev_last_ms = last_ms
                self._record_lap(lap_number, last_ms, valid=True)
            return

        prev_lap       = self._prev_lap
        self._prev_lap = cur_lap

        # Normal lap detection: lap counter increased with a new valid time
        if (
            cur_lap > prev_lap
            and last_ms >= _MIN_VALID_LAP_MS
            and last_ms != self._prev_last_ms
        ):
            self._prev_last_ms = last_ms
            valid = not self._lap_ever_invalid
            self._lap_ever_invalid = False   # reset for the new lap
            self._record_lap(int(prev_lap), last_ms, valid=valid)

        # Checkered flag detection: catches single-lap races and the final lap of
        # any race where the lap counter never increments past the finish line.
        elif checkered_edge and last_ms != self._prev_last_ms:
            if last_ms >= _MIN_VALID_LAP_MS:
                # last_lap_time was populated at finish
                self._prev_last_ms = last_ms
                valid = not self._lap_ever_invalid
                self._lap_ever_invalid = False
                self._record_lap(int(cur_lap), last_ms, valid=valid)
            else:
                # Fallback: use current_lap_time (some games don't set last_lap_time)
                cur_time_ms = _ms(telemetry.get("current_lap_time", 0.0))
                if cur_time_ms >= _MIN_VALID_LAP_MS:
                    valid = not self._lap_ever_invalid
                    self._lap_ever_invalid = False
                    self._record_lap(int(cur_lap), cur_time_ms, valid=valid)

    def _record_lap(self, lap_number: int, lap_ms: int, valid: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO laps (session_id, lap_number, lap_time_ms, valid, recorded_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (self._session_id, lap_number, lap_ms, int(valid),
                 datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()
        if self.on_lap_recorded:
            self.on_lap_recorded()

    # ── queries ───────────────────────────────────────────────────────────────

    def current_session_laps(self) -> list[tuple[int, int, bool]]:
        """Return [(lap_number, lap_time_ms, valid), ...] for the current session."""
        if self._session_id is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT lap_number, lap_time_ms, valid FROM laps"
                " WHERE session_id = ? ORDER BY lap_number",
                (self._session_id,),
            ).fetchall()
        return [(r[0], r[1], bool(r[2])) for r in rows]

    def session_history(self, limit: int = 50) -> list[tuple]:
        """Return summary rows for the most recent sessions.
        Columns: (id, started_at, game, vehicle, track, lap_count, best_ms, avg_ms)
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id, s.started_at, s.game, s.vehicle, s.track,
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

    def session_laps(self, session_id: int) -> list[tuple[int, int, bool]]:
        """Return [(lap_number, lap_time_ms, valid), ...] for any session by id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT lap_number, lap_time_ms, valid FROM laps"
                " WHERE session_id = ? ORDER BY lap_number",
                (session_id,),
            ).fetchall()
        return [(r[0], r[1], bool(r[2])) for r in rows]
