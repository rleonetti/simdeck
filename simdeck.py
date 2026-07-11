"""
SimDeck — sim racing companion app.

Controls LIFX lights via SimHub telemetry and splits UDP to multiple apps.
Run this file for the GUI experience.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
import webbrowser
from typing import Callable

__version__ = "1.0.0"
_RELEASES_URL = "https://api.github.com/repos/rleonetti/simdeck/releases/latest"
_RELEASES_PAGE = "https://github.com/rleonetti/simdeck/releases/latest"

import psutil

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QPalette, QPainter, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QCheckBox, QComboBox, QPushButton, QLineEdit,
    QFrame, QScrollArea, QDialog, QDialogButtonBox,
)
from PIL import Image, ImageDraw
import pystray

import config
import log_setup
import settings_manager
from effects import EFFECTS
from engine import Engine
from telemetry_logger import TelemetryLogger
from udp_splitter import UDPSplitter

log_setup.setup()

_GREEN   = "#2ecc71"
_YELLOW  = "#f0a500"
_GREY    = "#484848"
_MUTED   = "#888888"
_AMBER   = "#c07800"
_POLL_MS = 500
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_ALL_EFFECTS = ["rev_counter", "brake_lights", "flag_effect", "pit_limiter"]

_MODE_LABELS = {"left_right": "Left → Right", "center": "Center Fill", "full": "Full Strip"}
_MODE_VALUES = {v: k for k, v in _MODE_LABELS.items()}

_SCHEME_LABELS = {
    "classic": "Classic  (green → red, red flash)",
    "porsche": "Porsche  (green → red, blue flash)",
    "formula": "Formula  (red only, blue flash)",
    "icy":     "Icy      (green → blue, white flash)",
}
_SCHEME_VALUES = {v: k for k, v in _SCHEME_LABELS.items()}

_PIT_LIGHTS_OPTIONS: dict[str, list[str]] = {
    "Strip":          ["strip"],
    "Ceiling Lights": list(config.BRAKE_LIGHTS),
    "Both":           ["strip"] + list(config.BRAKE_LIGHTS),
}

# ── Game auto-target feature ──────────────────────────────────────────────────
# Set False to disable entirely — targets revert to manual checkbox control.
_AUTO_GAME_TARGETS = True

# Single source of truth for known games. Each entry maps a process name to a
# display name. Per-target game associations are stored on each splitter row.
_KNOWN_GAMES: list[dict[str, str]] = [
    {"name": "Forza Horizon 6",            "exe": "forzahorizon6.exe"},
    {"name": "Forza Motorsport",           "exe": "forzamotorsport.exe"},
    {"name": "Assetto Corsa EVO",          "exe": "AssettoCorsaEVO.exe"},#
    {"name": "Assetto Corsa Rally",        "exe": "acr.exe"},#
    {"name": "Assetto Corsa Competizione", "exe": "AC2-Win64-Shipping.exe"},#
    {"name": "Assetto Corsa",              "exe": "acs.exe"},
    {"name": "BeamNG.drive",               "exe": "beamng.drive.exe"},
    {"name": "iRacing",                    "exe": "iracingsim64dx11.exe"},
    {"name": "rFactor 2",                  "exe": "rfactor2.exe"},
]

_EXE_TO_NAME: dict[str, str] = {g["exe"]: g["name"] for g in _KNOWN_GAMES}


def _check_for_update() -> str | None:
    """Return the latest release tag (e.g. '1.2.0') if newer than __version__, else None."""
    try:
        req = urllib.request.Request(_RELEASES_URL, headers={"User-Agent": "SimDeck"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        tag = data.get("tag_name", "").lstrip("v")
        if not tag:
            return None
        def _ver(s):
            try:
                return tuple(int(x) for x in s.split("."))
            except ValueError:
                return (0,)
        return tag if _ver(tag) > _ver(__version__) else None
    except Exception:
        return None


def _detect_game() -> tuple[str | None, str | None]:
    """Return (exe, display_name) of the first known racing game found running, or (None, None)."""
    if not _AUTO_GAME_TARGETS:
        return None, None
    try:
        running = {p.name().lower() for p in psutil.process_iter(["name"])}
        for game in _KNOWN_GAMES:
            if game["exe"].lower() in running:
                return game["exe"], game["name"]
    except Exception:
        pass
    return None, None


def _fmt_time(ms: int) -> str:
    """Format milliseconds as M:SS.mmm (e.g. 1:23.456)."""
    if ms <= 0:
        return "—"
    m = ms // 60_000
    s = (ms % 60_000) / 1000.0
    return f"{m}:{s:06.3f}"


def _fmt_delta(ms: int) -> str:
    """Format a signed millisecond delta as ±S.mmm."""
    if ms == 0:
        return "—"
    sign = "+" if ms > 0 else ""
    return f"{sign}{ms / 1000:.3f}"


def _dot_color(status: str) -> str:
    return {"connected": _GREEN, "connecting": _YELLOW, "waiting": _YELLOW}.get(status, _GREY)


def _make_tray_image(simhub_status: str = "disconnected") -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 62, 62], fill=(90, 90, 90, 255))
    if simhub_status == "connected":
        dot = (46, 204, 113, 255)    # green
    elif simhub_status in ("waiting", "connecting"):
        dot = (240, 165, 0, 255)     # amber
    else:
        dot = (255, 255, 255, 40)    # faint white (offline)
    draw.ellipse([18, 18, 46, 46], fill=dot)
    return img


def _make_window_icon() -> QIcon:
    size = 64
    pix  = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(0xC0, 0x78, 0x00))  # amber
    f = QFont()
    f.setBold(True)
    f.setPixelSize(48)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "SD")
    p.end()
    return QIcon(pix)


def _apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(28, 28, 28))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(210, 210, 210))
    p.setColor(QPalette.ColorRole.Base,            QColor(38, 38, 38))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.Text,            QColor(210, 210, 210))
    p.setColor(QPalette.ColorRole.Button,          QColor(50, 50, 50))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(210, 210, 210))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(40, 110, 220))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(240, 240, 240))
    p.setColor(QPalette.ColorRole.Mid,             QColor(60, 60, 60))
    p.setColor(QPalette.ColorRole.Dark,            QColor(20, 20, 20))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(100, 100, 100))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(100, 100, 100))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(100, 100, 100))
    app.setPalette(p)


class _UISignal(QObject):
    """Dispatch callables from worker threads to the main thread via signal."""
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(lambda fn: fn())


class _NoScrollSlider(QSlider):
    def wheelEvent(self, event) -> None:
        event.ignore()


# ─────────────────────────────────────────────────────────────────────────────
# LIFX Effects tab
# ─────────────────────────────────────────────────────────────────────────────

class LIFXTab(QWidget):
    def __init__(
        self,
        engine: Engine,
        settings: dict,
        on_change: Callable,
        on_force_restart: Callable,
        ui: _UISignal,
    ) -> None:
        super().__init__()
        self._engine           = engine
        self._on_change        = on_change
        self._on_force_restart = on_force_restart
        self._ui               = ui
        self._light_dots:          dict[str, QLabel]    = {}
        self._light_effect_labels: dict[str, QLabel]    = {}
        self._effect_checks:       dict[str, QCheckBox] = {}
        self._effect_groups:       dict[str, tuple]     = {}

        self._spinner_idx   = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._spin)

        self._build(settings)
        self._wire_autosave()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self, s: dict) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Status bar
        bar_w = QWidget()
        bar   = QHBoxLayout(bar_w)
        bar.setContentsMargins(12, 10, 12, 6)
        sh_hdr = QLabel("SimHub")
        sh_hdr.setStyleSheet("font-weight: bold;")
        bar.addWidget(sh_hdr)
        self._sh_dot = QLabel("●")
        self._sh_dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        bar.addWidget(self._sh_dot)
        self._sh_lbl = QLabel("Disconnected")
        self._sh_lbl.setStyleSheet(f"color: {_MUTED};")
        bar.addWidget(self._sh_lbl)
        bar.addStretch()
        self._restart_btn = QPushButton("Restart Effects")
        self._restart_btn.setFixedSize(130, 28)
        self._restart_btn.clicked.connect(self._on_force_restart)
        bar.addWidget(self._restart_btn)
        root.addWidget(bar_w)

        # Two-column body
        body_w = QWidget()
        body   = QHBoxLayout(body_w)
        body.setContentsMargins(8, 0, 8, 8)
        body.setSpacing(4)
        root.addWidget(body_w, stretch=1)

        # ── Left: lights list ──────────────────────────────────────────────
        left_f = QFrame()
        left_f.setFrameShape(QFrame.Shape.StyledPanel)
        left_l = QVBoxLayout(left_f)
        left_l.setContentsMargins(10, 8, 10, 8)
        left_l.setSpacing(0)
        left_l.setAlignment(Qt.AlignmentFlag.AlignTop)

        hdr_w = QWidget()
        hdr_h = QHBoxLayout(hdr_w)
        hdr_h.setContentsMargins(0, 0, 0, 4)
        hdr_h.setSpacing(4)
        lights_hdr = QLabel("LIGHTS")
        lights_hdr.setStyleSheet(f"font-size: 19px; font-weight: bold; color: {_MUTED};")
        hdr_h.addWidget(lights_hdr)
        self._spinner_lbl = QLabel("")
        self._spinner_lbl.setStyleSheet(f"font-size: 17px; color: {_YELLOW};")
        hdr_h.addWidget(self._spinner_lbl)
        hdr_h.addStretch()
        left_l.addWidget(hdr_w)

        for name in config.LIFX_LIGHTS:
            cell_w = QWidget()
            cell_v = QVBoxLayout(cell_w)
            cell_v.setContentsMargins(0, 4, 0, 0)
            cell_v.setSpacing(1)

            nr_w = QWidget()
            nr_h = QHBoxLayout(nr_w)
            nr_h.setContentsMargins(0, 0, 0, 0)
            nr_h.setSpacing(6)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {_GREY}; font-size: 18px;")
            nr_h.addWidget(dot)
            nm = QLabel(name)
            nm.setStyleSheet("font-size: 18px;")
            nr_h.addWidget(nm)
            nr_h.addStretch()
            cell_v.addWidget(nr_w)

            eff_lbl = QLabel("")
            eff_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 14px; padding-left: 22px;")
            cell_v.addWidget(eff_lbl)

            left_l.addWidget(cell_w)
            self._light_dots[name]          = dot
            self._light_effect_labels[name] = eff_lbl

        left_l.addStretch()
        body.addWidget(left_f)

        # ── Right: scrollable settings ─────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        right_w = QWidget()
        right   = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 4, 8)
        right.setSpacing(0)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(right_w)
        body.addWidget(scroll, stretch=1)

        # EFFECTS toggles — 2 per row
        self._section(right, "EFFECTS")
        active = s.get("active_effects", config.ACTIVE_EFFECTS)
        eff_grid_w = QWidget()
        eff_grid   = QGridLayout(eff_grid_w)
        eff_grid.setContentsMargins(10, 2, 10, 2)
        eff_grid.setSpacing(4)
        for i, name in enumerate(_ALL_EFFECTS):
            cb = QCheckBox(name.replace("_", " ").title())
            cb.setChecked(name in active)
            eff_grid.addWidget(cb, i // 2, i % 2)
            self._effect_checks[name] = cb
        right.addWidget(eff_grid_w)

        # REV COUNTER
        rev_hdr   = self._section(right, "REV COUNTER")
        rev_group = QWidget()
        rev_v     = QVBoxLayout(rev_group)
        rev_v.setContentsMargins(10, 0, 10, 4)
        rev_v.setSpacing(3)

        mr_w = QWidget()
        mr_h = QHBoxLayout(mr_w)
        mr_h.setContentsMargins(0, 0, 0, 0)
        mr_h.setSpacing(6)
        mr_h.addWidget(QLabel("Mode"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(list(_MODE_LABELS.values()))
        self._mode_combo.setCurrentText(_MODE_LABELS.get(s["counter_mode"], s["counter_mode"]))
        self._mode_combo.setFixedWidth(130)
        mr_h.addWidget(self._mode_combo)
        mr_h.addSpacing(16)
        mr_h.addWidget(QLabel("Reversed"))
        self._reversed_cb = QCheckBox()
        self._reversed_cb.setChecked(s["strip_reversed"])
        mr_h.addWidget(self._reversed_cb)
        mr_h.addStretch()
        rev_v.addWidget(mr_w)

        self._start_rpm = self._slider(rev_v, "Start RPM",       s["start_rpm"],                    0, 10000)
        self._redline   = self._slider(rev_v, "Redline %",        s["redline_pct"],                  80, 100)
        self._strip_bri = self._slider(rev_v, "Max Brightness %", s.get("strip_brightness_pct", 50), 10, 100)

        cs_w = QWidget()
        cs_h = QHBoxLayout(cs_w)
        cs_h.setContentsMargins(0, 0, 0, 0)
        cs_lbl = QLabel("Color Scheme")
        cs_lbl.setFixedWidth(120)
        cs_h.addWidget(cs_lbl)
        self._scheme_combo = QComboBox()
        self._scheme_combo.addItems(list(_SCHEME_LABELS.values()))
        self._scheme_combo.setCurrentText(
            _SCHEME_LABELS.get(s.get("color_scheme", "classic"), _SCHEME_LABELS["classic"])
        )
        cs_h.addWidget(self._scheme_combo, stretch=1)
        rev_v.addWidget(cs_w)

        ls_w = QWidget()
        ls_h = QHBoxLayout(ls_w)
        ls_h.setContentsMargins(0, 0, 0, 0)
        ls_lbl = QLabel("LED Step")
        ls_lbl.setFixedWidth(120)
        ls_h.addWidget(ls_lbl)
        self._led_step_combo = QComboBox()
        self._led_step_combo.addItems(["1", "2", "4", "8"])
        self._led_step_combo.setCurrentText(str(s["led_step"]))
        self._led_step_combo.setFixedWidth(80)
        ls_h.addWidget(self._led_step_combo)
        hint = QLabel("(full mode only)")
        hint.setStyleSheet(f"color: {_MUTED}; font-size: 15px;")
        ls_h.addWidget(hint)
        ls_h.addStretch()
        rev_v.addWidget(ls_w)

        right.addWidget(rev_group)
        self._effect_groups["rev_counter"] = (rev_hdr, rev_group)

        # BRAKE LIGHTS
        brake_hdr   = self._section(right, "BRAKE LIGHTS")
        brake_group = QWidget()
        brake_v     = QVBoxLayout(brake_group)
        brake_v.setContentsMargins(10, 0, 10, 4)
        brake_v.setSpacing(3)
        self._brake_thr = self._slider(brake_v, "Threshold %",  s["brake_threshold_pct"],  0,  20)
        self._brake_bri = self._slider(brake_v, "Brightness %", s["brake_brightness_pct"],  0, 100)
        right.addWidget(brake_group)
        self._effect_groups["brake_lights"] = (brake_hdr, brake_group)

        # FLAG EFFECT
        flag_hdr   = self._section(right, "FLAG EFFECT")
        flag_group = QWidget()
        flag_v     = QVBoxLayout(flag_group)
        flag_v.setContentsMargins(10, 0, 10, 4)
        flag_v.setSpacing(3)
        self._flag_bri = self._slider(flag_v, "Brightness %", s.get("flag_brightness_pct", 100), 0, 100)
        right.addWidget(flag_group)
        self._effect_groups["flag_effect"] = (flag_hdr, flag_group)

        # PIT LIMITER
        pit_hdr   = self._section(right, "PIT LIMITER")
        pit_group = QWidget()
        pit_v     = QVBoxLayout(pit_group)
        pit_v.setContentsMargins(10, 0, 10, 4)
        pit_v.setSpacing(3)
        pl_w = QWidget()
        pl_h = QHBoxLayout(pl_w)
        pl_h.setContentsMargins(0, 0, 0, 0)
        pl_lbl = QLabel("Lights")
        pl_lbl.setFixedWidth(120)
        pl_h.addWidget(pl_lbl)
        self._pit_lights_combo = QComboBox()
        self._pit_lights_combo.addItems(list(_PIT_LIGHTS_OPTIONS.keys()))
        self._pit_lights_combo.setCurrentText(s.get("pit_limiter_lights_label", "Strip"))
        self._pit_lights_combo.setFixedWidth(160)
        pl_h.addWidget(self._pit_lights_combo)
        pl_h.addStretch()
        pit_v.addWidget(pl_w)
        self._pit_bri = self._slider(pit_v, "Brightness %", s.get("pit_limiter_brightness_pct", 75), 0, 100)
        right.addWidget(pit_group)
        self._effect_groups["pit_limiter"] = (pit_hdr, pit_group)

        right.addStretch()

        # Apply initial dimmed state
        for name, cb in self._effect_checks.items():
            if name in self._effect_groups:
                self._set_group_state(name, cb.isChecked())

    def _section(self, layout: QVBoxLayout, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {_MUTED};"
            " padding-top: 12px; padding-bottom: 2px; padding-left: 10px;"
        )
        layout.addWidget(lbl)
        return lbl

    def _slider(self, layout: QVBoxLayout, label: str, value: int, from_: int, to: int) -> _NoScrollSlider:
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(6)

        lbl = QLabel(label)
        lbl.setFixedWidth(120)
        row_h.addWidget(lbl)

        sl = _NoScrollSlider(Qt.Orientation.Horizontal)
        sl.setRange(from_, to)
        sl.setValue(value)
        sl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row_h.addWidget(sl, stretch=1)

        val_lbl = QLabel(str(value))
        val_lbl.setFixedWidth(36)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {_MUTED};")
        row_h.addWidget(val_lbl)

        sl.valueChanged.connect(lambda v, vl=val_lbl: vl.setText(str(v)))
        layout.addWidget(row_w)
        return sl

    # ── group state ───────────────────────────────────────────────────────────

    def _set_group_state(self, effect_name: str, enabled: bool) -> None:
        if effect_name not in self._effect_groups:
            return
        hdr, group = self._effect_groups[effect_name]
        color = "#d5d5d5" if enabled else _MUTED
        hdr.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {color};"
            " padding-top: 12px; padding-bottom: 2px; padding-left: 10px;"
        )
        group.setEnabled(enabled)

    # ── wiring ────────────────────────────────────────────────────────────────

    def _wire_autosave(self) -> None:
        for sl in (self._start_rpm, self._redline, self._strip_bri,
                   self._brake_thr, self._brake_bri, self._flag_bri, self._pit_bri):
            sl.valueChanged.connect(lambda _: self._on_change())
        for cb in (self._mode_combo, self._scheme_combo, self._led_step_combo, self._pit_lights_combo):
            cb.currentTextChanged.connect(lambda _: self._on_change())
        self._reversed_cb.stateChanged.connect(lambda _: self._on_change())
        for name, cb in self._effect_checks.items():
            def _make_toggle(nm, c):
                def _on_toggle(_):
                    self._set_group_state(nm, c.isChecked())
                    self._on_change()
                return _on_toggle
            cb.stateChanged.connect(_make_toggle(name, cb))

    # ── state ─────────────────────────────────────────────────────────────────

    def mark_pending(self, pending: bool) -> None:
        if pending:
            self._restart_btn.setStyleSheet(
                f"QPushButton {{ background-color: {_AMBER}; color: white; }}"
            )
            self._restart_btn.setText("Apply Changes")
        else:
            self._restart_btn.setStyleSheet("")
            self._restart_btn.setText("Restart Effects")

    def set_restart_state(self, enabled: bool, text: str) -> None:
        self._restart_btn.setEnabled(enabled)
        self._restart_btn.setText(text)

    # ── data ──────────────────────────────────────────────────────────────────

    def get_effect_kwargs(self) -> dict:
        return {
            "active_effects":       [n for n, cb in self._effect_checks.items() if cb.isChecked()],
            "start_rpm":            self._start_rpm.value(),
            "start_threshold":      config.REV_START_THRESHOLD,
            "redline_threshold":    self._redline.value() / 100.0,
            "flash_interval":       config.REV_FLASH_INTERVAL,
            "transition_ms":        config.LIFX_TRANSITION_MS,
            "led_step":             int(self._led_step_combo.currentText()),
            "counter_mode":         _MODE_VALUES.get(self._mode_combo.currentText(), "center"),
            "strip_reversed":       self._reversed_cb.isChecked(),
            "strip_max_brightness": self._strip_bri.value() / 100.0,
            "color_scheme":         _SCHEME_VALUES.get(self._scheme_combo.currentText(), "classic"),
            "brake_lights":               config.BRAKE_LIGHTS,
            "brake_threshold":            self._brake_thr.value() / 100.0,
            "brake_max_brightness":       self._brake_bri.value() / 100.0,
            "flag_lights":                config.FLAG_LIGHTS,
            "flag_max_brightness":        self._flag_bri.value() / 100.0,
            "pit_limiter_lights":         _PIT_LIGHTS_OPTIONS.get(self._pit_lights_combo.currentText(), ["strip"]),
            "pit_limiter_brightness":     self._pit_bri.value() / 100.0,
            "pit_limiter_flash_interval": config.PIT_LIMITER_FLASH_INTERVAL,
        }

    def get_settings(self) -> dict:
        return {
            "active_effects":             [n for n, cb in self._effect_checks.items() if cb.isChecked()],
            "color_scheme":               _SCHEME_VALUES.get(self._scheme_combo.currentText(), "classic"),
            "counter_mode":               _MODE_VALUES.get(self._mode_combo.currentText(), "center"),
            "strip_reversed":             self._reversed_cb.isChecked(),
            "strip_brightness_pct":       self._strip_bri.value(),
            "led_step":                   int(self._led_step_combo.currentText()),
            "start_rpm":                  self._start_rpm.value(),
            "redline_pct":                self._redline.value(),
            "brake_threshold_pct":        self._brake_thr.value(),
            "brake_brightness_pct":       self._brake_bri.value(),
            "flag_brightness_pct":        self._flag_bri.value(),
            "pit_limiter_lights_label":   self._pit_lights_combo.currentText(),
            "pit_limiter_brightness_pct": self._pit_bri.value(),
        }

    def _compute_assignments(self) -> dict[str, list[str]]:
        kwargs  = self.get_effect_kwargs()
        mapping: dict[str, list[str]] = {}
        for name in kwargs.get("active_effects", []):
            cls = EFFECTS.get(name)
            if cls and hasattr(cls, "needed_lights"):
                for light in cls.needed_lights(**kwargs):
                    mapping.setdefault(light, []).append(name)
        return mapping

    # ── spinner ───────────────────────────────────────────────────────────────

    def _spin(self) -> None:
        self._spinner_lbl.setText(_SPINNER[self._spinner_idx % len(_SPINNER)])
        self._spinner_idx += 1

    def _start_spinner(self) -> None:
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        self._spinner_timer.stop()
        self._spinner_lbl.setText("")

    # ── poll ──────────────────────────────────────────────────────────────────

    def poll(self, status: dict, telemetry: dict) -> None:
        sh = status["simhub"]
        self._sh_dot.setStyleSheet(f"color: {_dot_color(sh)}; font-size: 20px;")
        if sh == "connected" and telemetry:
            rpm     = int(telemetry.get("rpm", 0))
            max_rpm = int(telemetry.get("max_rpm", 0))
            brake   = int(telemetry.get("brake", 0))
            self._sh_lbl.setText(f"Connected   {rpm:,} / {max_rpm:,} rpm   Brake {brake}%")
        else:
            self._sh_lbl.setText({"waiting": "Waiting for data…"}.get(sh, "Disconnected"))

        any_connecting = any(v == "connecting" for v in status["lights"].values())
        if any_connecting:
            self._start_spinner()
        else:
            self._stop_spinner()

        for name, dot in self._light_dots.items():
            dot.setStyleSheet(
                f"color: {_dot_color(status['lights'].get(name, 'offline'))}; font-size: 18px;"
            )

        assignments = self._compute_assignments()
        for name, lbl in self._light_effect_labels.items():
            effects_for = assignments.get(name, [])
            lbl.setText(" · ".join(e.replace("_", " ") for e in effects_for))


# ─────────────────────────────────────────────────────────────────────────────
# Test tab
# ─────────────────────────────────────────────────────────────────────────────

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
        title = QLabel("Test Harness")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        hdr_h.addWidget(title)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        hdr_h.addWidget(self._dot)
        hdr_h.addStretch()
        self._toggle_btn = QPushButton("Activate")
        self._toggle_btn.setFixedWidth(110)
        self._toggle_btn.clicked.connect(self._toggle)
        hdr_h.addWidget(self._toggle_btn)
        v.addWidget(hdr_w)

        desc = QLabel(
            "Activating test mode stops the live engine and connects directly to LIFX for animation testing."
        )
        desc.setStyleSheet(f"color: {_MUTED};")
        desc.setWordWrap(True)
        v.addWidget(desc)

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

        from test_harness import TestPanel
        self._panel = TestPanel(self._panel_frame, self._ui)
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


# ─────────────────────────────────────────────────────────────────────────────
# UDP Splitter tab
# ─────────────────────────────────────────────────────────────────────────────

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
    """Checkbox picker for associating a splitter target with specific games."""

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
        self._splitter  = splitter
        self._on_change       = on_change
        self._rows: list[dict] = []
        self._active_game_exe: str | None = None
        self._build(settings)

    def _build(self, s: dict) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        # Status bar
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
        self._targets_layout = QVBoxLayout(self._targets_frame)
        self._targets_layout.setContentsMargins(10, 6, 10, 6)
        self._targets_layout.setSpacing(2)

        # Column headers
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
        port_e.setFixedWidth(60)
        row_h.addWidget(port_e)

        lbl_e = QLineEdit()
        lbl_e.setPlaceholderText("Note (optional)")
        if label:
            lbl_e.setText(label)
        lbl_e.setFixedWidth(130)
        row_h.addWidget(lbl_e)

        games_btn = QPushButton(_games_btn_text(row_games))
        games_btn.setFixedWidth(110)
        row_h.addWidget(games_btn)

        row_h.addStretch()

        entry = {
            "widget":  row_w,
            "ip":      ip_e,
            "port":    port_e,
            "label":   lbl_e,
            "enabled": en_cb,
            "games":   row_games,
            "games_btn": games_btn,
            "fields":  (ip_e, colon, port_e, lbl_e, games_btn),
            "removed": False,
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

        if not enabled:
            for f in entry["fields"]:
                f.setEnabled(False)

        def remove(e=entry):
            e["removed"] = True
            e["widget"].setParent(None)
            self._on_change()

        rm_btn = QPushButton("✕")
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet("QPushButton { background: transparent; } QPushButton:hover { background: #442222; }")
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
                # Row has a game list — only include if the active game is in it
                if self._active_game_exe not in r["games"]:
                    continue
            # Empty games list = game-agnostic, always include
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


# ─────────────────────────────────────────────────────────────────────────────
# Logger tab
# ─────────────────────────────────────────────────────────────────────────────

class LoggerTab(QWidget):
    def __init__(self, logger: TelemetryLogger, ui: _UISignal) -> None:
        super().__init__()
        self._logger          = logger
        self._ui              = ui
        self._game: str | None = None
        self._was_connected   = False
        self._build()
        logger.on_lap_recorded = lambda: self._ui.call.emit(self._refresh_lap_table)

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Status bar
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
        self._auto_cb.stateChanged.connect(self._on_auto_changed)
        bar_h.addWidget(self._auto_cb)
        self._toggle_btn = QPushButton("Start Recording")
        self._toggle_btn.setFixedWidth(140)
        self._toggle_btn.clicked.connect(self._toggle)
        bar_h.addWidget(self._toggle_btn)
        root.addWidget(bar_w)

        # Live stats row
        stats_w = QWidget()
        stats_h = QHBoxLayout(stats_w)
        stats_h.setContentsMargins(0, 0, 0, 0)
        stats_h.setSpacing(8)
        self._stat_lap  = self._stat_box(stats_h, "LAP",      "—")
        self._stat_cur  = self._stat_box(stats_h, "CURRENT",  "—")
        self._stat_last = self._stat_box(stats_h, "LAST",     "—")
        self._stat_best, self._stat_best_hdr = self._stat_box(stats_h, "FAST LAP", "—", return_hdr=True)
        root.addWidget(stats_w)

        # Lap table header label
        lap_hdr = QLabel("LAP HISTORY")
        lap_hdr.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {_MUTED}; padding-top: 4px;"
        )
        root.addWidget(lap_hdr)

        # Lap table
        self._lap_table = self._make_lap_table()
        root.addWidget(self._lap_table, stretch=1)

        # Footer: summary
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 16px;")
        root.addWidget(self._summary_lbl)

    def _stat_box(self, layout: QHBoxLayout, label: str, value: str,
                  return_hdr: bool = False) -> "QLabel | tuple[QLabel, QLabel]":
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
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

    def _make_lap_table(self) -> "QTableWidget":
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        from PySide6.QtCore import Qt as _Qt
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

    # ── auto record ───────────────────────────────────────────────────────────

    def _on_auto_changed(self) -> None:
        # If the user enables auto-record while SimHub is already live, start immediately.
        if self._auto_cb.isChecked() and self._was_connected and not self._logger.recording:
            self._start_recording()

    # ── toggle recording ──────────────────────────────────────────────────────

    def _start_recording(self) -> None:
        self._logger.start_session(game=self._game)
        self._rec_dot.setStyleSheet(f"color: #e74c3c; font-size: 20px;")
        self._rec_lbl.setText("Recording")
        self._rec_lbl.setStyleSheet("color: #e74c3c;")
        self._toggle_btn.setText("Stop Recording")
        self._refresh_lap_table()

    def _stop_recording(self) -> None:
        self._logger.stop_session()
        self._rec_dot.setStyleSheet(f"color: {_GREY}; font-size: 20px;")
        self._rec_lbl.setText("Not recording")
        self._rec_lbl.setStyleSheet(f"color: {_MUTED};")
        self._toggle_btn.setText("Start Recording")

    def _toggle(self) -> None:
        if self._logger.recording:
            self._stop_recording()
        else:
            self._start_recording()

    # ── refresh ───────────────────────────────────────────────────────────────

    def _refresh_lap_table(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt as _Qt
        laps = self._logger.current_session_laps()   # [(lap_num, ms, valid), ...]

        valid_times = [ms for _, ms, v in laps if v]
        best_ms = min(valid_times, default=0)
        avg_ms  = int(sum(valid_times) / len(valid_times)) if valid_times else 0

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
                item.setTextAlignment(_Qt.AlignmentFlag.AlignCenter)
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

    # ── poll ──────────────────────────────────────────────────────────────────

    def poll(self, telemetry: dict, simhub_status: str, game: str | None) -> None:
        self._game = game

        if game:
            self._game_lbl.setText(f"  {game}")
        else:
            self._game_lbl.setText("")

        # Auto-record: start when SimHub connects, stop when it drops
        is_connected = simhub_status == "connected"
        if self._auto_cb.isChecked():
            if is_connected and not self._was_connected and not self._logger.recording:
                self._start_recording()
            elif not is_connected and self._was_connected and self._logger.recording:
                self._stop_recording()
        self._was_connected = is_connected

        if not telemetry:
            return

        cur_lap     = telemetry.get("current_lap")
        cur_time    = telemetry.get("current_lap_time", 0.0)
        last_t      = telemetry.get("last_lap_time",    0.0)
        best_t      = telemetry.get("best_lap_time",    0.0)
        checkered   = telemetry.get("flag_checkered",   0.0)

        if cur_lap is not None:
            self._stat_lap.setText(str(int(cur_lap)))
        self._stat_cur.setText(_fmt_time(int(cur_time * 1000)) if cur_time else "—")
        self._stat_last.setText(_fmt_time(int(last_t * 1000))  if last_t  else "—")
        self._stat_best.setText(_fmt_time(int(best_t * 1000))  if best_t  else "—")
        self._stat_best_hdr.setText("BEST LAP" if checkered else "FAST LAP")


# ─────────────────────────────────────────────────────────────────────────────
# History tab
# ─────────────────────────────────────────────────────────────────────────────

class HistoryTab(QWidget):
    # column indices in sessions table
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
        from PySide6.QtWidgets import QTableWidget, QHeaderView, QSplitter
        from PySide6.QtCore import Qt as _Qt

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        splitter = QSplitter(_Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # ── Top: sessions ──────────────────────────────────────────────────
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

        # ── Bottom: laps for selected session ─────────────────────────────
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

    # ── data ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt as _Qt

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
                item.setTextAlignment(_Qt.AlignmentFlag.AlignCenter)
                if c == self._COL_DATE:
                    item.setData(_Qt.ItemDataRole.UserRole, sid)
                self._sessions_tbl.setItem(r, c, item)

        self._delete_btn.setEnabled(False)
        self._laps_tbl.setRowCount(0)
        self._laps_hdr.setText("LAPS")
        self._laps_summary.setText("Select a session above to see its laps.")

    def _delete_selected(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt as _Qt

        sel = self._sessions_tbl.selectedItems()
        if not sel:
            return
        row  = sel[0].row()
        sid  = self._sessions_tbl.item(row, self._COL_DATE).data(_Qt.ItemDataRole.UserRole)
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
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt as _Qt

        sel = self._sessions_tbl.selectedItems()
        if not sel:
            self._delete_btn.setEnabled(False)
            return
        self._delete_btn.setEnabled(True)

        row     = sel[0].row()
        sid     = self._sessions_tbl.item(row, self._COL_DATE).data(_Qt.ItemDataRole.UserRole)
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
                item.setTextAlignment(_Qt.AlignmentFlag.AlignCenter)
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


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class SimDeckApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SimDeck")
        self.setWindowIcon(_make_window_icon())
        self.resize(860, 740)
        self.setMinimumSize(760, 620)

        self._ui       = _UISignal()
        settings       = settings_manager.load()
        self._tray     = None

        self._logger   = TelemetryLogger()
        self._engine   = Engine()
        self._splitter = UDPSplitter(
            listen_port=settings["splitter_port"],
            targets=[(t["ip"], t["port"]) for t in settings["splitter_targets"]],
        )

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_v = QVBoxLayout(central)
        main_v.setContentsMargins(10, 10, 10, 0)
        main_v.setSpacing(0)

        main_tabs = QTabWidget()
        main_v.addWidget(main_tabs, stretch=1)

        # ── Light Control ──────────────────────────────────────────────────
        light_tabs = QTabWidget()

        self._lifx_tab = LIFXTab(
            engine=self._engine,
            settings=settings,
            on_change=self._on_lifx_change,
            on_force_restart=self._force_restart,
            ui=self._ui,
        )
        light_tabs.addTab(self._lifx_tab, "LIFX Effects")

        self._splitter_tab = SplitterTab(self._splitter, settings, self._save_settings)
        light_tabs.addTab(self._splitter_tab, "UDP Splitter")

        self._test_tab = TestTab(self._engine, self._lifx_tab.get_effect_kwargs, self._ui)
        light_tabs.addTab(self._test_tab, "Test")

        main_tabs.addTab(light_tabs, "Light Control")

        # ── Lap Logs ───────────────────────────────────────────────────────
        lap_tabs = QTabWidget()

        self._logger_tab = LoggerTab(self._logger, self._ui)
        lap_tabs.addTab(self._logger_tab, "Logger")

        self._history_tab = HistoryTab(self._logger)
        lap_tabs.addTab(self._history_tab, "History")

        main_tabs.addTab(lap_tabs, "Lap Logs")

        # Refresh history when the user switches to the Lap Logs main tab
        def _on_main_tab_changed(idx: int) -> None:
            if main_tabs.tabText(idx) == "Lap Logs":
                self._history_tab.refresh()

        main_tabs.currentChanged.connect(_on_main_tab_changed)


        # Debounce timer
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.setInterval(1500)
        self._restart_timer.timeout.connect(self._auto_restart)

        # Poll timer
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        self._last_game_exe: str | None = None
        self._game_timer = QTimer(self)
        self._game_timer.setInterval(20000)
        self._game_timer.timeout.connect(self._check_game)
        self._game_timer.start()

        self._last_tray_status: str | None = None
        self._update_version: str | None = None
        self._setup_tray()

        initial_kwargs = self._lifx_tab.get_effect_kwargs()
        self._engine.start(initial_kwargs)
        self._splitter.start()

        threading.Thread(target=self._bg_update_check, daemon=True).start()

    # ── settings ──────────────────────────────────────────────────────────────

    def _save_settings(self) -> None:
        settings = {}
        settings.update(self._lifx_tab.get_settings())
        settings.update(self._splitter_tab.get_settings())
        settings_manager.save(settings)

    def _on_lifx_change(self) -> None:
        self._save_settings()
        self._lifx_tab.mark_pending(True)
        self._restart_timer.start()

    def _auto_restart(self) -> None:
        self._lifx_tab.mark_pending(False)
        self._do_restart()

    def _force_restart(self) -> None:
        self._restart_timer.stop()
        self._lifx_tab.mark_pending(False)
        self._do_restart()

    def _do_restart(self) -> None:
        self._lifx_tab.set_restart_state(False, "Restarting…")
        kwargs = self._lifx_tab.get_effect_kwargs()

        def _worker() -> None:
            self._engine.pause()
            self._engine.resume(kwargs)
            self._ui.call.emit(lambda: self._lifx_tab.set_restart_state(True, "Restart Effects"))

        threading.Thread(target=_worker, daemon=True).start()

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        status    = self._engine.get_status()
        telemetry = self._engine.get_telemetry()
        self._lifx_tab.poll(status, telemetry)
        self._splitter_tab.poll()
        self._logger.feed(telemetry)
        self._logger_tab.poll(
            telemetry,
            status["simhub"],
            self._last_game_exe and _EXE_TO_NAME.get(self._last_game_exe),
        )

        sh = status["simhub"]
        if sh != self._last_tray_status and self._tray:
            self._tray.icon = _make_tray_image(sh)
            self._last_tray_status = sh

    def _check_game(self) -> None:
        exe, display = _detect_game()
        if exe != self._last_game_exe:
            self._last_game_exe = exe
            self._splitter_tab.set_active_game(exe, display)

    # ── tray ──────────────────────────────────────────────────────────────────

    def _to_tray(self) -> None:
        self.hide()

    def _restore(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _bg_update_check(self) -> None:
        latest = _check_for_update()
        if latest:
            self._update_version = latest
            self._ui.call.emit(lambda: self._tray.update_menu())

    def _manual_update_check(self) -> None:
        def _worker() -> None:
            latest = _check_for_update()
            if latest:
                self._update_version = latest
                self._ui.call.emit(lambda: self._tray.update_menu())
                try:
                    self._tray.notify(f"Update available: v{latest} — click tray menu to download.", "SimDeck")
                except Exception:
                    pass
            else:
                try:
                    self._tray.notify(f"SimDeck v{__version__} is up to date.", "SimDeck")
                except Exception:
                    pass
        threading.Thread(target=_worker, daemon=True).start()

    def _setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Open", lambda *_: self._ui.call.emit(self._restore), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: f"Update available: v{self._update_version} — click to download",
                lambda *_: webbrowser.open(_RELEASES_PAGE),
                visible=lambda item: self._update_version is not None,
            ),
            pystray.MenuItem("Check for Update", lambda *_: self._manual_update_check()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restart Effects", lambda *_: self._ui.call.emit(self._force_restart)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self._ui.call.emit(self._quit)),
        )
        self._tray = pystray.Icon("SimDeck", _make_tray_image(), "SimDeck", menu)
        self._tray.run_detached()

    def _quit(self) -> None:
        self._poll_timer.stop()
        self._game_timer.stop()
        self._engine.stop()
        self._splitter.stop()
        if self._tray:
            self._tray.stop()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        event.ignore()
        self._to_tray()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _apply_dark_theme(app)
    window = SimDeckApp()
    window.show()
    sys.exit(app.exec())
