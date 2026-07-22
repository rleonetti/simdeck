"""Settings tab."""
from __future__ import annotations

import webbrowser
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget, QComboBox,
)

from simdeck import config
from ..constants import _GREEN, _GREY, _MUTED, _RELEASES_PAGE, __version__
from ..helpers import _get_startup_registry, _set_startup_registry


class SettingsTab(QWidget):
    def __init__(self,
                 settings: dict,
                 on_font_change: Callable[[int], None],
                 on_check_update: Callable[[], None],
                 on_startup_change: Callable[[bool, bool], None],
                 on_simhub_change: Callable[[str, int], None],
                 on_accent_change: Callable[[str], None] | None = None,
                 on_install_update: Callable[[str], None] | None = None,
                 on_pulse_change: Callable[[bool], None] | None = None,
                 on_overlay_change: Callable[[bool], None] | None = None,
                 on_overlay_theme_change: Callable[[str], None] | None = None,
                 on_overlay_bg_opacity_change: Callable[[int], None] | None = None,
                 on_overlay_line_opacity_change: Callable[[int], None] | None = None,
                 on_overlay_scale_change: Callable[[int], None] | None = None,
                 on_overlay_preview_toggle: Callable[[bool], None] | None = None,
                 on_gradient_change: Callable[[bool], None] | None = None) -> None:
        super().__init__()
        self._on_font_change                  = on_font_change
        self._on_check_update                 = on_check_update
        self._on_startup_change               = on_startup_change
        self._on_simhub_change                = on_simhub_change
        self._on_accent_change                = on_accent_change or (lambda _: None)
        self._on_install_update               = on_install_update
        self._on_pulse_change                 = on_pulse_change or (lambda _: None)
        self._on_overlay_change               = on_overlay_change or (lambda _: None)
        self._on_overlay_theme_change         = on_overlay_theme_change or (lambda _: None)
        self._on_overlay_bg_opacity_change    = on_overlay_bg_opacity_change or (lambda _: None)
        self._on_overlay_line_opacity_change  = on_overlay_line_opacity_change or (lambda _: None)
        self._on_overlay_scale_change         = on_overlay_scale_change or (lambda _: None)
        self._on_overlay_preview_toggle       = on_overlay_preview_toggle or (lambda _: None)
        self._on_gradient_change              = on_gradient_change or (lambda _: None)
        self._download_url: str               = ""
        self._build(settings)

    def _build(self, settings: dict) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(28, 20, 28, 20)

        content = QWidget()
        content.setMaximumWidth(560)
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # ── APPEARANCE ────────────────────────────────────────────────────────
        cv.addWidget(self._section_hdr("APPEARANCE"))
        cv.addSpacing(10)

        font_row = QHBoxLayout()
        font_row.setSpacing(10)
        font_lbl = QLabel("Font size")
        font_lbl.setFixedWidth(100)
        font_row.addWidget(font_lbl)

        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(9, 14)
        self._font_slider.setValue(settings.get("font_size_pt", 10))
        self._font_slider.setFixedWidth(200)
        self._font_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._font_slider.setTickInterval(1)
        font_row.addWidget(self._font_slider)

        self._font_val_lbl = QLabel(f"{self._font_slider.value()} pt")
        self._font_val_lbl.setStyleSheet(f"color: {_MUTED};")
        self._font_val_lbl.setFixedWidth(40)
        font_row.addWidget(self._font_val_lbl)
        font_row.addStretch()
        cv.addLayout(font_row)

        hint = QLabel("Affects table rows, buttons and tab labels.")
        hint.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        cv.addSpacing(4)
        cv.addWidget(hint)
        self._font_slider.valueChanged.connect(self._on_font_slider)

        cv.addSpacing(12)
        accent_row = QHBoxLayout()
        accent_row.setSpacing(10)
        accent_lbl = QLabel("Accent color")
        accent_lbl.setFixedWidth(100)
        accent_row.addWidget(accent_lbl)

        self._accent_swatch = QPushButton()
        self._accent_swatch.setFixedSize(64, 26)
        self._accent_swatch.setToolTip("Click to choose accent color")
        self._accent_swatch.clicked.connect(self._pick_accent)
        accent_row.addWidget(self._accent_swatch)

        accent_hint = QLabel("Color for sliders, checkboxes and the active tab.")
        accent_hint.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        accent_row.addWidget(accent_hint)
        accent_row.addStretch()
        cv.addLayout(accent_row)

        self._accent_color = settings.get("accent_color", "#f0a500")
        self._update_accent_swatch()

        cv.addSpacing(8)
        gradient_cb = QCheckBox("Gradient background")
        gradient_cb.setChecked(settings.get("gradient_bg", True))
        gradient_cb.setToolTip("Fade the window background diagonally from black/grey into the accent color")
        gradient_cb.stateChanged.connect(lambda _: self._on_gradient_change(gradient_cb.isChecked()))
        cv.addWidget(gradient_cb)

        # ── UPDATES ───────────────────────────────────────────────────────────
        cv.addSpacing(16)
        cv.addWidget(self._make_sep())
        cv.addSpacing(6)
        cv.addWidget(self._section_hdr("UPDATES"))
        cv.addSpacing(10)

        ver_lbl = QLabel(f"SimDeck  v{__version__}")
        ver_lbl.setStyleSheet("font-size: 15px;")
        cv.addWidget(ver_lbl)
        cv.addSpacing(8)

        update_row = QHBoxLayout()
        update_row.setSpacing(12)
        self._check_btn = QPushButton("Check for Update")
        self._check_btn.setFixedWidth(160)
        self._check_btn.clicked.connect(self._on_check_clicked)
        update_row.addWidget(self._check_btn)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")
        update_row.addWidget(self._update_status_lbl)
        update_row.addStretch()
        cv.addLayout(update_row)
        cv.addSpacing(6)

        self._download_btn = QPushButton("Update Now")
        self._download_btn.setVisible(False)
        self._download_btn.clicked.connect(self._on_update_now_clicked)
        cv.addWidget(self._download_btn)

        cv.addSpacing(10)
        pulse_cb = QCheckBox("Animate update indicator")
        pulse_cb.setChecked(settings.get("update_dot_pulse", True))
        pulse_cb.setToolTip("Pulse the green dot on the Settings tab when an update is available")
        pulse_cb.stateChanged.connect(lambda _: self._on_pulse_change(pulse_cb.isChecked()))
        cv.addWidget(pulse_cb)

        # ── OVERLAY ───────────────────────────────────────────────────────────
        cv.addSpacing(16)
        cv.addWidget(self._make_sep())
        cv.addSpacing(6)
        cv.addWidget(self._section_hdr("OVERLAY"))
        cv.addSpacing(10)

        overlay_desc = QLabel("Transparent always-on-top graph: throttle (green) and brake (red) traces.")
        overlay_desc.setStyleSheet(f"color: {_MUTED};")
        cv.addWidget(overlay_desc)
        cv.addSpacing(8)

        overlay_cb = QCheckBox("Show telemetry overlay")
        overlay_cb.setChecked(settings.get("overlay_visible", False))
        overlay_cb.setToolTip("Display the floating brake/throttle input graph")
        overlay_cb.stateChanged.connect(lambda _: self._on_overlay_change(overlay_cb.isChecked()))
        cv.addWidget(overlay_cb)
        cv.addSpacing(8)

        preview_btn = QPushButton("Preview with sample data")
        preview_btn.setCheckable(True)
        preview_btn.setToolTip(
            "Show the overlay animated with fake pedal input — no SimHub "
            "connection or pedal hardware required. Handy for positioning "
            "and styling the overlay before you're on track."
        )
        preview_btn_off_style = ""
        preview_btn_on_style = (
            f"QPushButton {{ background-color: {_GREEN}; border-color: {_GREEN}; color: #10190f; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {_GREEN}; border-color: {_GREEN}; }}"
        )

        def _on_preview_toggled(checked: bool) -> None:
            preview_btn.setText("● Previewing — click to stop" if checked else "Preview with sample data")
            preview_btn.setStyleSheet(preview_btn_on_style if checked else preview_btn_off_style)
            self._on_overlay_preview_toggle(checked)

        preview_btn.toggled.connect(_on_preview_toggled)
        cv.addWidget(preview_btn)
        cv.addSpacing(8)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_lbl = QLabel("Style")
        theme_lbl.setFixedWidth(130)
        theme_row.addWidget(theme_lbl)
        theme_combo = QComboBox()
        theme_combo.addItem("Mirrored (fill)",  "mirrored")
        theme_combo.addItem("Lines (baseline)", "lines")
        theme_combo.addItem("Bars (meters)",    "bars")
        saved_theme = settings.get("overlay_theme", "mirrored")
        idx = theme_combo.findData(saved_theme)
        theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        theme_combo.currentIndexChanged.connect(
            lambda _: self._on_overlay_theme_change(theme_combo.currentData())
        )
        theme_row.addWidget(theme_combo)
        theme_row.addStretch()
        cv.addLayout(theme_row)
        cv.addSpacing(10)

        def _ovl_slider_row(label: str, key: str, default: int,
                            lo: int, hi: int, tick: int,
                            unit: str, cb: Callable) -> None:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label)
            lbl.setFixedWidth(130)
            row.addWidget(lbl)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(settings.get(key, default))
            sl.setFixedWidth(180)
            sl.setTickPosition(QSlider.TickPosition.TicksBelow)
            sl.setTickInterval(tick)
            row.addWidget(sl)
            val_lbl = QLabel(f"{sl.value()}{unit}")
            val_lbl.setStyleSheet(f"color: {_MUTED};")
            val_lbl.setFixedWidth(44)
            row.addWidget(val_lbl)
            row.addStretch()
            cv.addLayout(row)
            cv.addSpacing(6)
            sl.valueChanged.connect(lambda v, vl=val_lbl, u=unit, c=cb: (vl.setText(f"{v}{u}"), c(v)))

        _ovl_slider_row("Background opacity", "overlay_bg_opacity",   70,  0, 100, 20, "%",
                        self._on_overlay_bg_opacity_change)
        _ovl_slider_row("Line opacity",        "overlay_line_opacity", 100, 10, 100, 20, "%",
                        self._on_overlay_line_opacity_change)
        _ovl_slider_row("Scale",               "overlay_scale",        100, 50, 200, 25, "%",
                        self._on_overlay_scale_change)

        cv.addSpacing(10)
        moza_row = QHBoxLayout()
        moza_row.setSpacing(8)
        moza_dot = QLabel("●")
        moza_dot.setFixedWidth(14)
        moza_dot.setStyleSheet(f"color: {_GREY};")
        moza_row.addWidget(moza_dot)
        self._moza_status_lbl = QLabel("Moza pedals: searching…")
        self._moza_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        moza_row.addWidget(self._moza_status_lbl)
        moza_row.addStretch()
        cv.addLayout(moza_row)
        self._moza_dot = moza_dot

        # ── STARTUP ───────────────────────────────────────────────────────────
        cv.addSpacing(16)
        cv.addWidget(self._make_sep())
        cv.addSpacing(6)
        cv.addWidget(self._section_hdr("STARTUP"))
        cv.addSpacing(10)

        self._launch_chk = QCheckBox("Launch SimDeck at Windows startup")
        self._launch_chk.setChecked(_get_startup_registry())
        self._launch_chk.toggled.connect(self._on_startup_toggled)
        cv.addWidget(self._launch_chk)
        cv.addSpacing(6)

        self._minimized_chk = QCheckBox("Start minimized to tray")
        self._minimized_chk.setChecked(settings.get("start_minimized", False))
        self._minimized_chk.toggled.connect(self._on_startup_toggled)
        cv.addWidget(self._minimized_chk)

        # ── CONNECTION ────────────────────────────────────────────────────────
        cv.addSpacing(16)
        cv.addWidget(self._make_sep())
        cv.addSpacing(6)
        cv.addWidget(self._section_hdr("CONNECTION"))
        cv.addSpacing(10)

        conn_desc = QLabel("SimHub Property Server TCP address")
        conn_desc.setStyleSheet(f"color: {_MUTED};")
        cv.addWidget(conn_desc)
        cv.addSpacing(8)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(8)
        conn_row.addWidget(QLabel("Host"))
        self._host_edit = QLineEdit(settings.get("simhub_host", config.SIMHUB_HOST))
        self._host_edit.setFixedWidth(140)
        conn_row.addWidget(self._host_edit)
        conn_row.addSpacing(4)
        conn_row.addWidget(QLabel("Port"))
        self._port_edit = QLineEdit(str(settings.get("simhub_port", config.SIMHUB_PORT)))
        self._port_edit.setFixedWidth(70)
        conn_row.addWidget(self._port_edit)
        conn_row.addSpacing(8)
        apply_btn = QPushButton("Apply")
        apply_btn.setFixedWidth(80)
        apply_btn.clicked.connect(self._on_conn_apply)
        conn_row.addWidget(apply_btn)
        self._conn_status_lbl = QLabel("")
        self._conn_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")
        conn_row.addWidget(self._conn_status_lbl)
        conn_row.addStretch()
        cv.addLayout(conn_row)

        cv.addStretch()
        outer.addWidget(content)
        outer.addStretch()

    @staticmethod
    def _section_hdr(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {_MUTED};")
        return lbl

    @staticmethod
    def _make_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setStyleSheet(f"color: {_GREY};")
        return sep

    def _on_update_now_clicked(self, _=None) -> None:
        if self._on_install_update and self._download_url:
            self._on_install_update(self._download_url)
        else:
            webbrowser.open(_RELEASES_PAGE)

    def _update_accent_swatch(self) -> None:
        self._accent_swatch.setStyleSheet(
            f"background-color: {self._accent_color}; border-radius: 4px;"
            f" border: 1px solid #555555; padding: 0;"
        )

    def _pick_accent(self, _=None) -> None:
        color = QColorDialog.getColor(QColor(self._accent_color), self, "Choose Accent Color")
        if color.isValid():
            self._accent_color = color.name()
            self._update_accent_swatch()
            self._on_accent_change(self._accent_color)

    def _on_font_slider(self, value: int) -> None:
        self._font_val_lbl.setText(f"{value} pt")
        self._on_font_change(value)

    def _on_check_clicked(self) -> None:
        self._check_btn.setEnabled(False)
        self._update_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")
        self._update_status_lbl.setText("Checking…")
        self._download_btn.setVisible(False)
        self._on_check_update()

    def set_update_result(self, version: str | None, url: str = "") -> None:
        self._check_btn.setEnabled(True)
        if version:
            self._download_url = url
            self._update_status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 14px;")
            self._update_status_lbl.setText(f"v{version} available")
            self._download_btn.setText(f"Update to v{version}")
            self._download_btn.setEnabled(True)
            self._download_btn.setVisible(True)
        else:
            self._update_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")
            self._update_status_lbl.setText("Up to date ✓")

    def set_update_available(self, version: str, url: str = "") -> None:
        self._download_url = url
        self._update_status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 14px;")
        self._update_status_lbl.setText(f"v{version} available")
        self._download_btn.setText(f"Update to v{version}")
        self._download_btn.setEnabled(True)
        self._download_btn.setVisible(True)

    def set_downloading(self) -> None:
        self._download_btn.setEnabled(False)
        self._update_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px;")
        self._update_status_lbl.setText("Downloading…")

    def set_download_error(self) -> None:
        self._download_btn.setEnabled(True)
        self._update_status_lbl.setStyleSheet("color: #cc4444; font-size: 14px;")
        self._update_status_lbl.setText("Download failed — try again")

    def set_moza_status(self, connected: bool, port: str | None) -> None:
        if connected:
            self._moza_dot.setStyleSheet(f"color: {_GREEN};")
            self._moza_status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 13px;")
            self._moza_status_lbl.setText(f"Moza pedals: connected ({port})")
        else:
            self._moza_dot.setStyleSheet(f"color: {_GREY};")
            self._moza_status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
            self._moza_status_lbl.setText("Moza pedals: searching…")

    def _on_startup_toggled(self) -> None:
        self._on_startup_change(
            self._launch_chk.isChecked(),
            self._minimized_chk.isChecked(),
        )

    def _on_conn_apply(self) -> None:
        host = self._host_edit.text().strip()
        try:
            port = int(self._port_edit.text().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._conn_status_lbl.setStyleSheet("color: #e74c3c; font-size: 14px;")
            self._conn_status_lbl.setText("Invalid port")
            return
        if not host:
            self._conn_status_lbl.setStyleSheet("color: #e74c3c; font-size: 14px;")
            self._conn_status_lbl.setText("Invalid host")
            return
        self._conn_status_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 14px;")
        self._conn_status_lbl.setText("Applied — restarting engine…")
        self._on_simhub_change(host, port)
