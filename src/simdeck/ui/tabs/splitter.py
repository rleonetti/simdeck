"""UDP Splitter tab."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from simdeck.udp_splitter import UDPSplitter
from ..constants import _AUTO_GAME_TARGETS, _EXE_TO_NAME, _GREEN, _GREY, _KNOWN_GAMES, _MUTED


def _games_btn_text(exes: list[str]) -> str:
    if not exes:
        return "All games"
    names = [_EXE_TO_NAME.get(e, e) for e in exes]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, {names[1]}"
    return f"{len(names)} games"


class _GameDialog(QDialog):
    def __init__(self, parent: QWidget, selected_exes: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Game associations")
        self.setModal(True)
        self.setMinimumWidth(300)

        v = QVBoxLayout(self)
        v.setSpacing(6)

        note = QLabel(
            "Forward to this target only when one of the checked games is running.\n"
            "Uncheck all to always forward regardless of game."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        v.addWidget(note)

        self._checks: dict[str, QCheckBox] = {}
        for game in _KNOWN_GAMES:
            cb = QCheckBox(game["name"])
            cb.setChecked(game["exe"] in selected_exes)
            v.addWidget(cb)
            self._checks[game["exe"]] = cb

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def selected_exes(self) -> list[str]:
        return [exe for exe, cb in self._checks.items() if cb.isChecked()]


class SplitterTab(QWidget):
    def __init__(self, splitter: UDPSplitter, settings: dict, on_change: Callable) -> None:
        super().__init__()
        self._splitter          = splitter
        self._on_change         = on_change
        self._rows:              list[dict]  = []
        self._active_game_exe:   str | None  = None
        self._build(settings)

    def _build(self, s: dict) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        bar_w = QWidget()
        bar_h = QHBoxLayout(bar_w)
        bar_h.setContentsMargins(0, 0, 0, 0)
        bar_h.setSpacing(6)
        status_hdr = QLabel("Status")
        status_hdr.setStyleSheet("font-weight: bold;")
        bar_h.addWidget(status_hdr)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        bar_h.addWidget(self._dot)
        self._status_lbl = QLabel("Stopped")
        self._status_lbl.setStyleSheet(f"color: {_MUTED};")
        bar_h.addWidget(self._status_lbl)
        bar_h.addStretch()
        self._packet_lbl = QLabel("")
        self._packet_lbl.setStyleSheet(f"color: {_MUTED};")
        bar_h.addWidget(self._packet_lbl)
        v.addWidget(bar_w)

        port_w = QWidget()
        port_h = QHBoxLayout(port_w)
        port_h.setContentsMargins(0, 0, 0, 0)
        port_lbl = QLabel("Listen Port")
        port_lbl.setFixedWidth(100)
        port_h.addWidget(port_lbl)
        self._port_entry = QLineEdit()
        self._port_entry.setText(str(s["splitter_port"]))
        self._port_entry.setFixedWidth(80)
        self._port_entry.textChanged.connect(lambda _: self._on_change())
        port_h.addWidget(self._port_entry)
        port_h.addStretch()
        v.addWidget(port_w)

        if _AUTO_GAME_TARGETS:
            game_bar_w = QWidget()
            game_bar_h = QHBoxLayout(game_bar_w)
            game_bar_h.setContentsMargins(0, 0, 0, 0)
            game_bar_h.setSpacing(6)
            game_hdr = QLabel("Game")
            game_hdr.setStyleSheet("font-weight: bold;")
            game_bar_h.addWidget(game_hdr)
            self._game_lbl = QLabel("No game detected")
            self._game_lbl.setStyleSheet(f"color: {_MUTED};")
            game_bar_h.addWidget(self._game_lbl)
            game_bar_h.addStretch()
            self._auto_cb = QCheckBox("Auto-manage")
            self._auto_cb.setChecked(True)
            self._auto_cb.stateChanged.connect(lambda _: self._refresh_targets())
            game_bar_h.addWidget(self._auto_cb)
            v.addWidget(game_bar_w)
        else:
            self._game_lbl = None
            self._auto_cb  = None

        targets_hdr = QLabel("FORWARD TARGETS")
        targets_hdr.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {_MUTED}; padding-top: 6px;"
        )
        v.addWidget(targets_hdr)

        self._targets_frame = QFrame()
        self._targets_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._targets_frame.setObjectName("sd_panel")
        self._targets_layout = QVBoxLayout(self._targets_frame)
        self._targets_layout.setContentsMargins(10, 6, 10, 6)
        self._targets_layout.setSpacing(2)

        col_hdr_w = QWidget()
        col_hdr_h = QHBoxLayout(col_hdr_w)
        col_hdr_h.setContentsMargins(0, 0, 0, 0)
        col_hdr_h.setSpacing(0)
        en_hdr = QLabel("On")
        en_hdr.setStyleSheet(f"font-size: 15px; color: {_MUTED};")
        en_hdr.setFixedWidth(30)
        col_hdr_h.addWidget(en_hdr)
        for text, width in (("Host", 116), ("Port", 72), ("Note", 136), ("Games", 0)):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"font-size: 15px; color: {_MUTED};")
            if width:
                lbl.setFixedWidth(width)
            col_hdr_h.addWidget(lbl)
        col_hdr_h.addStretch()
        self._targets_layout.addWidget(col_hdr_w)
        v.addWidget(self._targets_frame)

        for t in s["splitter_targets"]:
            self._add_row(
                t.get("ip", "127.0.0.1"), t.get("port", 0),
                t.get("label", ""), t.get("enabled", True), t.get("games", []),
            )

        add_btn = QPushButton("+ Add Target")
        add_btn.setFixedWidth(120)
        add_btn.clicked.connect(self._add_row_and_save)
        v.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        btn_w = QWidget()
        btn_h = QHBoxLayout(btn_w)
        btn_h.setContentsMargins(0, 4, 0, 0)
        btn_h.addStretch()
        self._toggle_btn = QPushButton("Stop")
        self._toggle_btn.setFixedWidth(100)
        self._toggle_btn.clicked.connect(self._toggle)
        btn_h.addWidget(self._toggle_btn)
        v.addWidget(btn_w)

        v.addStretch()

    def _add_row(
        self,
        ip: str = "127.0.0.1",
        port: int = 0,
        label: str = "",
        enabled: bool = True,
        games: list[str] | None = None,
    ) -> None:
        row_games: list[str] = list(games) if games else []

        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 2, 0, 2)
        row_h.setSpacing(4)

        en_cb = QCheckBox()
        en_cb.setChecked(enabled)
        en_cb.setFixedWidth(26)
        row_h.addWidget(en_cb)

        ip_e = QLineEdit()
        ip_e.setPlaceholderText("IP address")
        ip_e.setText(ip)
        ip_e.setFixedWidth(110)
        row_h.addWidget(ip_e)

        colon = QLabel(":")
        row_h.addWidget(colon)

        port_e = QLineEdit()
        port_e.setPlaceholderText("Port")
        if port:
            port_e.setText(str(port))
        port_e.setFixedWidth(75)
        row_h.addWidget(port_e)

        lbl_e = QLineEdit()
        lbl_e.setPlaceholderText("Note (optional)")
        if label:
            lbl_e.setText(label)
        lbl_e.setFixedWidth(130)
        row_h.addWidget(lbl_e)

        games_btn = QPushButton(_games_btn_text(row_games))
        games_btn.setMinimumWidth(140)
        row_h.addWidget(games_btn, stretch=1)

        entry = {
            "widget":    row_w,
            "ip":        ip_e,
            "port":      port_e,
            "label":     lbl_e,
            "enabled":   en_cb,
            "games":     row_games,
            "games_btn": games_btn,
            "fields":    (ip_e, colon, port_e, lbl_e, games_btn),
            "removed":   False,
        }
        self._rows.append(entry)

        def _open_games(_, e=entry):
            dlg = _GameDialog(self, e["games"])
            if dlg.exec() == QDialog.DialogCode.Accepted:
                e["games"] = dlg.selected_exes()
                e["games_btn"].setText(_games_btn_text(e["games"]))
                self._on_change()

        games_btn.clicked.connect(_open_games)

        def _set_enabled(_, e=entry):
            on = e["enabled"].isChecked()
            for f in e["fields"]:
                f.setEnabled(on)
            self._on_change()

        en_cb.stateChanged.connect(_set_enabled)

        for field in (ip_e, port_e, lbl_e):
            field.textChanged.connect(lambda _: self._on_change())

        if not enabled:
            for f in entry["fields"]:
                f.setEnabled(False)

        def remove(_, e=entry):
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Remove target",
                f"Remove {e['ip'].text() or 'this'} : {e['port'].text() or '?'} ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            e["removed"] = True
            e["widget"].setParent(None)
            self._on_change()

        rm_btn = QPushButton("✕")
        rm_btn.setFixedWidth(28)
        rm_btn.setToolTip("Remove this target")
        rm_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid transparent;"
            " padding: 0; color: #777777; border-radius: 4px; }"
            " QPushButton:hover { background: #442222; border-color: #663333; color: #ffaaaa; }"
        )
        rm_btn.clicked.connect(remove)
        row_h.addWidget(rm_btn)

        self._targets_layout.addWidget(row_w)

    def _add_row_and_save(self) -> None:
        self._add_row()
        self._on_change()

    def _live_targets(self) -> list[tuple[str, int]]:
        auto_on = (
            _AUTO_GAME_TARGETS
            and self._auto_cb is not None
            and self._auto_cb.isChecked()
            and self._active_game_exe is not None
        )
        targets = []
        for r in self._rows:
            if r["removed"] or not r["enabled"].isChecked():
                continue
            if auto_on and r["games"]:
                if self._active_game_exe not in r["games"]:
                    continue
            ip = r["ip"].text().strip()
            try:
                port = int(r["port"].text().strip())
            except ValueError:
                continue
            if ip and port:
                targets.append((ip, port))
        return targets

    def _refresh_targets(self) -> None:
        if self._splitter.running:
            self._splitter.set_targets(self._live_targets())

    def set_active_game(self, exe: str | None, display: str | None) -> None:
        self._active_game_exe = exe
        if self._game_lbl is not None:
            if display:
                self._game_lbl.setText(display)
                self._game_lbl.setStyleSheet(f"color: {_GREEN};")
            else:
                self._game_lbl.setText("No game detected")
                self._game_lbl.setStyleSheet(f"color: {_MUTED};")
        self._refresh_targets()

    def _toggle(self) -> None:
        if self._splitter.running:
            self._splitter.stop()
        else:
            try:
                self._splitter.listen_port = int(self._port_entry.text().strip())
            except ValueError:
                return
            self._splitter.set_targets(self._live_targets())
            self._splitter.start()
        self._on_change()

    def get_settings(self) -> dict:
        targets = []
        for r in self._rows:
            if r["removed"]:
                continue
            ip = r["ip"].text().strip()
            try:
                port = int(r["port"].text().strip())
            except ValueError:
                continue
            if ip and port:
                targets.append({
                    "ip":      ip,
                    "port":    port,
                    "label":   r["label"].text().strip(),
                    "enabled": r["enabled"].isChecked(),
                    "games":   list(r["games"]),
                })
        try:
            splitter_port = int(self._port_entry.text().strip())
        except ValueError:
            splitter_port = 20777
        return {"splitter_port": splitter_port, "splitter_targets": targets}

    def poll(self) -> None:
        if self._splitter.running:
            self._dot.setStyleSheet(f"color: {_GREEN}; font-size: 20px;")
            self._status_lbl.setText("Running")
            self._packet_lbl.setText(f"{self._splitter.packet_count:,} packets")
            self._toggle_btn.setText("Stop")
        else:
            self._dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
            self._status_lbl.setText("Stopped")
            self._packet_lbl.setText("")
            self._toggle_btn.setText("Start")
