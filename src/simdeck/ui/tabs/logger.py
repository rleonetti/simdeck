"""Lap logger tab — live recording and in-session lap history."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from simdeck.telemetry_logger import TelemetryLogger
from ..constants import _GREEN, _GREY, _MUTED
from ..helpers import _fmt_delta, _fmt_time
from ..widgets import _UISignal


class LoggerTab(QWidget):
    def __init__(self, logger: TelemetryLogger, ui: _UISignal) -> None:
        super().__init__()
        self._logger          = logger
        self._ui              = ui
        self._game: str | None = None
        self._was_connected   = False
        self._build()
        logger.on_lap_recorded = lambda: self._ui.call.emit(self._refresh_lap_table)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        bar_w = QWidget()
        bar_h = QHBoxLayout(bar_w)
        bar_h.setContentsMargins(0, 0, 0, 0)
        bar_h.setSpacing(8)

        self._rec_dot = QLabel("●")
        self._rec_dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        bar_h.addWidget(self._rec_dot)
        self._rec_lbl = QLabel("Not recording")
        self._rec_lbl.setStyleSheet(f"color: {_MUTED};")
        bar_h.addWidget(self._rec_lbl)
        self._game_lbl = QLabel("")
        self._game_lbl.setStyleSheet(f"color: {_MUTED};")
        bar_h.addWidget(self._game_lbl)
        bar_h.addStretch()
        self._auto_cb = QCheckBox("Auto Record")
        self._auto_cb.setChecked(True)
        self._auto_cb.stateChanged.connect(self._on_auto_changed)
        bar_h.addWidget(self._auto_cb)
        self._toggle_btn = QPushButton("Start Recording")
        self._toggle_btn.setFixedWidth(140)
        self._toggle_btn.clicked.connect(self._toggle)
        bar_h.addWidget(self._toggle_btn)
        root.addWidget(bar_w)

        self._session_meta_lbl = QLabel("")
        self._session_meta_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 16px; padding-left: 2px;")
        root.addWidget(self._session_meta_lbl)

        stats_w = QWidget()
        stats_h = QHBoxLayout(stats_w)
        stats_h.setContentsMargins(0, 0, 0, 0)
        stats_h.setSpacing(8)
        self._stat_lap  = self._stat_box(stats_h, "LAP",      "—")
        self._stat_cur  = self._stat_box(stats_h, "CURRENT",  "—")
        self._stat_last = self._stat_box(stats_h, "LAST",     "—")
        self._stat_best, self._stat_best_hdr = self._stat_box(stats_h, "FAST LAP", "—", return_hdr=True)
        root.addWidget(stats_w)

        lap_hdr = QLabel("LAP HISTORY")
        lap_hdr.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {_MUTED}; padding-top: 4px;"
        )
        root.addWidget(lap_hdr)

        self._lap_table = self._make_lap_table()
        root.addWidget(self._lap_table, stretch=1)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 16px;")
        root.addWidget(self._summary_lbl)

    def _stat_box(self, layout: QHBoxLayout, label: str, value: str,
                  return_hdr: bool = False) -> "QLabel | tuple[QLabel, QLabel]":
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setObjectName("sd_panel")
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 6, 10, 6)
        v.setSpacing(2)
        hdr = QLabel(label)
        hdr.setStyleSheet(f"font-size: 15px; color: {_MUTED};")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hdr)
        val = QLabel(value)
        val.setStyleSheet("font-size: 24px; font-weight: bold;")
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(val)
        layout.addWidget(frame, stretch=1)
        return (val, hdr) if return_hdr else val

    def _make_lap_table(self):
        from PySide6.QtWidgets import QTableWidget, QHeaderView
        tbl = QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels(["Lap", "Time", "Δ Best"])
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        return tbl

    def _on_auto_changed(self) -> None:
        if self._auto_cb.isChecked() and self._was_connected and not self._logger.recording:
            self._start_recording()

    def _start_recording(self) -> None:
        self._logger.start_session(game=self._game)
        self._rec_dot.setStyleSheet("color: #e74c3c; font-size: 20px;")
        self._rec_lbl.setText("Recording")
        self._rec_lbl.setStyleSheet("color: #e74c3c;")
        self._toggle_btn.setText("Stop Recording")
        self._refresh_lap_table()

    def _stop_recording(self) -> None:
        self._logger.flush_pending_lap()
        self._logger.stop_session()
        self._rec_dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        self._rec_lbl.setText("Not recording")
        self._rec_lbl.setStyleSheet(f"color: {_MUTED};")
        self._toggle_btn.setText("Start Recording")
        self._session_meta_lbl.setText("")

    def _toggle(self) -> None:
        if self._logger.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _refresh_lap_table(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        laps        = self._logger.current_session_laps()
        valid_times = [ms for _, ms, v in laps if v]
        best_ms     = min(valid_times, default=0)
        avg_ms      = int(sum(valid_times) / len(valid_times)) if valid_times else 0

        self._lap_table.setRowCount(len(laps))
        for row, (lap_num, lap_ms, valid) in enumerate(laps):
            is_best = valid and lap_ms == best_ms and best_ms > 0
            if not valid:
                delta_text = "INVALID"
            elif is_best:
                delta_text = "—"
            else:
                delta_text = _fmt_delta(lap_ms - best_ms)

            items = [
                QTableWidgetItem(str(lap_num)),
                QTableWidgetItem(_fmt_time(lap_ms)),
                QTableWidgetItem(delta_text),
            ]
            for col, item in enumerate(items):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not valid:
                    item.setForeground(QColor(_MUTED))
                elif is_best:
                    item.setForeground(QColor(_GREEN))
                self._lap_table.setItem(row, col, item)

        self._lap_table.scrollToBottom()

        if laps:
            import statistics
            consistency = ""
            if len(valid_times) >= 2:
                stdev_s = statistics.stdev(valid_times) / 1000.0
                consistency = f"  ·  Consistency ±{stdev_s:.2f}s"
            invalid_count = sum(1 for _, _, v in laps if not v)
            invalid_note  = f"  ·  {invalid_count} invalid" if invalid_count else ""
            self._summary_lbl.setText(
                f"{len(laps)} lap{'s' if len(laps) != 1 else ''}  ·  "
                f"Avg {_fmt_time(avg_ms)}{consistency}{invalid_note}"
            )
        else:
            self._summary_lbl.setText("")

    def poll(self, telemetry: dict, simhub_status: str, game: str | None) -> None:
        self._game = game

        if game:
            self._game_lbl.setText(f"  {game}")
            self._logger.update_session_game(game)
        else:
            self._game_lbl.setText("")

        is_connected = simhub_status == "connected"
        if self._auto_cb.isChecked():
            if is_connected and not self._was_connected and not self._logger.recording:
                self._start_recording()
            elif not is_connected and self._was_connected and self._logger.recording:
                self._stop_recording()
        self._was_connected = is_connected

        if not telemetry:
            return

        cur_lap   = telemetry.get("current_lap")
        cur_time  = telemetry.get("current_lap_time", 0.0)
        last_t    = telemetry.get("last_lap_time",    0.0)
        best_t    = telemetry.get("best_lap_time",    0.0)
        checkered = telemetry.get("flag_checkered",   0.0)
        vehicle   = telemetry.get("vehicle", "") or ""
        track     = telemetry.get("track",   "") or ""

        def _readable(s: str) -> bool:
            return bool(s) and not all(c.isupper() or c.isdigit() or c in "_- " for c in s)
        meta_parts = [p for p in [vehicle, track] if _readable(p)]
        self._session_meta_lbl.setText("  ·  ".join(meta_parts))

        if cur_lap is not None:
            self._stat_lap.setText(str(int(cur_lap)))
        self._stat_cur.setText(_fmt_time(int(cur_time * 1000)) if cur_time else "—")
        self._stat_last.setText(_fmt_time(int(last_t  * 1000)) if last_t  else "—")
        self._stat_best.setText(_fmt_time(int(best_t  * 1000)) if best_t  else "—")
        self._stat_best_hdr.setText("BEST LAP" if checkered else "FAST LAP")
