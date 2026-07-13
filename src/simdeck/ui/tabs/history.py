"""Lap history tab — browse and delete past recorded sessions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from simdeck.telemetry_logger import TelemetryLogger
from ..constants import _GREEN, _MUTED
from ..helpers import _fmt_delta, _fmt_time


class HistoryTab(QWidget):
    _COL_DATE    = 0
    _COL_GAME    = 1
    _COL_VEHICLE = 2
    _COL_TRACK   = 3
    _COL_LAPS    = 4
    _COL_BEST    = 5
    _COL_AVG     = 6

    def __init__(self, logger: TelemetryLogger) -> None:
        super().__init__()
        self._logger = logger
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        top_w = QWidget()
        top_v = QVBoxLayout(top_w)
        top_v.setContentsMargins(0, 0, 0, 0)
        top_v.setSpacing(4)

        hdr_w = QWidget()
        hdr_h = QHBoxLayout(hdr_w)
        hdr_h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("SESSIONS")
        lbl.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {_MUTED};")
        hdr_h.addWidget(lbl)
        hdr_h.addStretch()
        self._delete_btn = QPushButton("Delete Session")
        self._delete_btn.setFixedWidth(120)
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        hdr_h.addWidget(self._delete_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self.refresh)
        hdr_h.addWidget(refresh_btn)
        top_v.addWidget(hdr_w)

        self._sessions_tbl = QTableWidget(0, 7)
        self._sessions_tbl.setHorizontalHeaderLabels(
            ["Date", "Game", "Vehicle", "Track", "Laps", "Best", "Avg"]
        )
        hdr = self._sessions_tbl.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_DATE,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_GAME,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_VEHICLE, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self._COL_TRACK,   QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self._COL_LAPS,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_BEST,    QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self._COL_AVG,     QHeaderView.ResizeMode.ResizeToContents)
        self._sessions_tbl.verticalHeader().setVisible(False)
        self._sessions_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sessions_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sessions_tbl.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._sessions_tbl.setAlternatingRowColors(True)
        self._sessions_tbl.itemSelectionChanged.connect(self._on_session_selected)
        top_v.addWidget(self._sessions_tbl)
        splitter.addWidget(top_w)

        bot_w = QWidget()
        bot_v = QVBoxLayout(bot_w)
        bot_v.setContentsMargins(0, 0, 0, 0)
        bot_v.setSpacing(4)

        self._laps_hdr = QLabel("LAPS")
        self._laps_hdr.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {_MUTED};")
        bot_v.addWidget(self._laps_hdr)

        self._laps_tbl = QTableWidget(0, 3)
        self._laps_tbl.setHorizontalHeaderLabels(["Lap", "Time", "Δ Best"])
        self._laps_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._laps_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._laps_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._laps_tbl.verticalHeader().setVisible(False)
        self._laps_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._laps_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._laps_tbl.setAlternatingRowColors(True)
        bot_v.addWidget(self._laps_tbl)

        self._laps_summary = QLabel("")
        self._laps_summary.setStyleSheet(f"color: {_MUTED}; font-size: 16px;")
        bot_v.addWidget(self._laps_summary)
        splitter.addWidget(bot_w)

        splitter.setSizes([220, 320])
        root.addWidget(splitter)

    def refresh(self) -> None:
        rows = self._logger.session_history()
        self._sessions_tbl.setRowCount(len(rows))
        for r, (sid, started, game, vehicle, track, lap_count, best_ms, avg_ms) in enumerate(rows):
            date_str = started[:16].replace("T", "  ") if started else "—"
            values   = [
                date_str,
                game    or "—",
                vehicle or "—",
                track   or "—",
                str(lap_count or 0),
                _fmt_time(best_ms or 0),
                _fmt_time(avg_ms  or 0),
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == self._COL_DATE:
                    item.setData(Qt.ItemDataRole.UserRole, sid)
                self._sessions_tbl.setItem(r, c, item)

        self._delete_btn.setEnabled(False)
        self._laps_tbl.setRowCount(0)
        self._laps_hdr.setText("LAPS")
        self._laps_summary.setText("Select a session above to see its laps.")

    def _delete_selected(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        sel = self._sessions_tbl.selectedItems()
        if not sel:
            return
        row  = sel[0].row()
        sid  = self._sessions_tbl.item(row, self._COL_DATE).data(Qt.ItemDataRole.UserRole)
        date = self._sessions_tbl.item(row, self._COL_DATE).text()
        game = self._sessions_tbl.item(row, self._COL_GAME).text()

        reply = QMessageBox.question(
            self,
            "Delete Session",
            f"Delete session from {date} ({game}) and all its laps?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._logger.delete_session(sid)
        self.refresh()

    def _on_session_selected(self) -> None:
        sel = self._sessions_tbl.selectedItems()
        if not sel:
            self._delete_btn.setEnabled(False)
            return
        self._delete_btn.setEnabled(True)

        row     = sel[0].row()
        sid     = self._sessions_tbl.item(row, self._COL_DATE).data(Qt.ItemDataRole.UserRole)
        game    = self._sessions_tbl.item(row, self._COL_GAME).text()
        vehicle = self._sessions_tbl.item(row, self._COL_VEHICLE).text()
        track   = self._sessions_tbl.item(row, self._COL_TRACK).text()
        n_laps  = self._sessions_tbl.item(row, self._COL_LAPS).text()
        best    = self._sessions_tbl.item(row, self._COL_BEST).text()
        date    = self._sessions_tbl.item(row, self._COL_DATE).text()

        parts = [p for p in [game, vehicle, track] if p and p != "—"]
        meta  = "  ·  ".join(parts) if parts else "—"
        self._laps_hdr.setText(
            f"LAPS  ·  {date}  ·  {meta}  ·  {n_laps} laps  ·  Best {best}"
        )

        laps        = self._logger.session_laps(sid)
        valid_times = [ms for _, ms, v in laps if v]
        best_ms     = min(valid_times, default=0)

        self._laps_tbl.setRowCount(len(laps))
        for r, (lap_num, lap_ms, valid) in enumerate(laps):
            is_best    = valid and lap_ms == best_ms and best_ms > 0
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
            for c, item in enumerate(items):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not valid:
                    item.setForeground(QColor(_MUTED))
                elif is_best:
                    item.setForeground(QColor(_GREEN))
                self._laps_tbl.setItem(r, c, item)

        if laps:
            import statistics
            consistency = ""
            if len(valid_times) >= 2:
                stdev_s     = statistics.stdev(valid_times) / 1000.0
                consistency = f"  ·  Consistency ±{stdev_s:.2f}s"
            invalid_count = sum(1 for _, _, v in laps if not v)
            invalid_note  = f"  ·  {invalid_count} invalid" if invalid_count else ""
            avg_ms        = int(sum(valid_times) / len(valid_times)) if valid_times else 0
            self._laps_summary.setText(
                f"Avg {_fmt_time(avg_ms)}{consistency}{invalid_note}"
            )
        else:
            self._laps_summary.setText("No laps recorded in this session.")
