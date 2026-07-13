"""Test tab — pauses the live engine and drives LIFX with simulated telemetry."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from simdeck.engine import Engine
from ..constants import _GREEN, _GREY, _MUTED, _YELLOW
from ..widgets import _UISignal


class TestTab(QWidget):
    def __init__(self, engine: Engine, get_effect_kwargs: Callable, ui: _UISignal) -> None:
        super().__init__()
        self._engine            = engine
        self._get_effect_kwargs = get_effect_kwargs
        self._ui                = ui
        self._panel             = None
        self._active            = False
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        hdr_w = QWidget()
        hdr_h = QHBoxLayout(hdr_w)
        hdr_h.setContentsMargins(0, 0, 0, 0)
        desc = QLabel("Pauses the live engine and drives LIFX directly with simulated telemetry.")
        desc.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        desc.setWordWrap(True)
        hdr_h.addWidget(desc, stretch=1)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {_GREY}; font-size: 18px;")
        hdr_h.addWidget(self._dot)
        self._toggle_btn = QPushButton("Activate")
        self._toggle_btn.setFixedWidth(100)
        self._toggle_btn.clicked.connect(self._toggle)
        hdr_h.addWidget(self._toggle_btn)
        v.addWidget(hdr_w)

        self._panel_frame = QWidget()
        pf_v = QVBoxLayout(self._panel_frame)
        pf_v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._panel_frame, stretch=1)

    def _toggle(self) -> None:
        if self._active:
            self._deactivate()
        else:
            self._activate()

    def _activate(self) -> None:
        self._active = True
        self._engine.pause()
        shared_rig = self._engine.get_rig()

        self._toggle_btn.setEnabled(False)
        self._toggle_btn.setText("Connecting…" if shared_rig is None else "Activating…")
        self._dot.setStyleSheet(f"color: {_YELLOW}; font-size: 20px;")

        try:
            from test_harness import TestPanel
        except ImportError:
            self._dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
            self._toggle_btn.setEnabled(True)
            self._toggle_btn.setText("Activate")
            self._active = False
            self._engine.resume(self._get_effect_kwargs())
            return

        self._panel = TestPanel(self._panel_frame, self._ui,
                                get_effect_kwargs=self._get_effect_kwargs)
        self._panel_frame.layout().addWidget(self._panel)

        def _do() -> None:
            self._panel.connect(shared_rig=shared_rig)
            self._ui.call.emit(lambda: self._toggle_btn.setEnabled(True))
            self._ui.call.emit(lambda: self._toggle_btn.setText("Deactivate"))
            self._ui.call.emit(lambda: self._dot.setStyleSheet(f"color: {_GREEN}; font-size: 20px;"))

        threading.Thread(target=_do, daemon=True).start()

    def _deactivate(self) -> None:
        self._active = False
        if self._panel:
            self._panel.disconnect()
            self._panel.setParent(None)
            self._panel = None
        self._dot.setStyleSheet(f"color: {_YELLOW}; font-size: 20px;")
        self._toggle_btn.setEnabled(False)
        self._toggle_btn.setText("Resuming…")

        kwargs = self._get_effect_kwargs()

        def _do() -> None:
            self._engine.resume(kwargs)
            self._ui.call.emit(lambda: self._toggle_btn.setEnabled(True))
            self._ui.call.emit(lambda: self._toggle_btn.setText("Activate"))
            self._ui.call.emit(lambda: self._dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;"))

        threading.Thread(target=_do, daemon=True).start()
