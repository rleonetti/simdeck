"""
SimDeck — sim racing companion app.

Controls LIFX lights via SimHub telemetry and splits UDP to multiple apps.
Run this file for the GUI experience.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
import webbrowser
from collections import deque
from typing import Callable

__version__ = "1.3.2"
_RELEASES_URL = "https://api.github.com/repos/rleonetti/simdeck/releases/latest"
_RELEASES_PAGE = "https://github.com/rleonetti/simdeck/releases/latest"

import psutil

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QColor, QPalette, QPainter, QFont, QIcon, QPixmap, QIntValidator, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QTabBar,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSlider, QCheckBox, QComboBox, QPushButton, QLineEdit,
    QFrame, QScrollArea, QDialog, QDialogButtonBox, QColorDialog,
    QListWidget,
)
from PIL import Image, ImageDraw
import pystray

import config
import log_setup
import settings_manager
from effects import EFFECTS
from engine import Engine
from moza_pedals import MozaPedals
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

_FLAG_ORDER: list[str] = [
    "flag_yellow", "flag_red", "flag_blue", "flag_white",
    "flag_green", "flag_checkered", "flag_black",
]
_FLAG_DISPLAY: dict[str, str] = {
    "flag_yellow":    "Yellow",
    "flag_red":       "Red",
    "flag_blue":      "Blue",
    "flag_white":     "White",
    "flag_green":     "Green",
    "flag_checkered": "Checkered",
    "flag_black":     "Black (penalty)",
}
_FLAG_DESC: dict[str, str] = {
    "flag_yellow":    "Caution, no overtaking",
    "flag_red":       "Session stopped",
    "flag_blue":      "Let faster car through",
    "flag_white":     "Slow vehicle / last lap",
    "flag_green":     "Race start or restart",
    "flag_checkered": "Session finished",
    "flag_black":     "Penalty / disqualification",
}
_FLAG_DOT_COLOR: dict[str, str] = {
    "flag_yellow":    "#f0c000",
    "flag_red":       "#e74c3c",
    "flag_blue":      "#3b82f6",
    "flag_white":     "#cccccc",
    "flag_green":     "#2ecc71",
    "flag_checkered": "#cccccc",
    "flag_black":     "#e74c3c",
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

# ── Windows registry helpers (launch at startup) ──────────────────────────────
try:
    import winreg as _winreg
    _HAS_WINREG = True
except ImportError:
    _HAS_WINREG = False

_STARTUP_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_REG_NAME = "SimDeck"


def _get_startup_registry() -> bool:
    if not _HAS_WINREG:
        return False
    try:
        with _winreg.OpenKey(
            _winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, _winreg.KEY_READ
        ) as key:
            val, _ = _winreg.QueryValueEx(key, _STARTUP_REG_NAME)
            return sys.executable.lower() in val.lower()
    except (FileNotFoundError, OSError):
        return False


def _set_startup_registry(enabled: bool) -> None:
    if not _HAS_WINREG:
        return
    try:
        with _winreg.OpenKey(
            _winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, _winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                _winreg.SetValueEx(key, _STARTUP_REG_NAME, 0,
                                   _winreg.REG_SZ, f'"{sys.executable}"')
            else:
                try:
                    _winreg.DeleteValue(key, _STARTUP_REG_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def _apply_font_size(size_pt: int) -> None:
    app = QApplication.instance()
    if app:
        font = app.font()
        font.setPointSize(size_pt)
        app.setFont(font)


def _check_for_update() -> tuple[str, str] | None:
    """Return (version, download_url) if a newer release is available, else None."""
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
        if _ver(tag) <= _ver(__version__):
            return None
        url = next(
            (a["browser_download_url"] for a in data.get("assets", [])
             if a["name"].lower().endswith(".exe")),
            _RELEASES_PAGE,
        )
        return tag, url
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


_APP_STYLESHEET_TPL = """
QPushButton {
    border-radius: 6px;
    padding: 4px 10px;
    border: 1px solid #404040;
    background-color: #2e2e2e;
}
QPushButton:hover   { background-color: #383838; border-color: #525252; }
QPushButton:pressed { background-color: #262626; }
QPushButton:disabled { color: #505050; border-color: #333333; }

QLineEdit {
    border-radius: 5px;
    border: 1px solid #404040;
    padding: 4px 8px;
    background-color: #222222;
}
QLineEdit:focus { border-color: %%DARK%%; }

QComboBox {
    border-radius: 5px;
    border: 1px solid #404040;
    padding: 3px 8px;
    background-color: #222222;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox:hover { border-color: #525252; }
QComboBox QAbstractItemView {
    background-color: #222222;
    border: 1px solid #404040;
    selection-background-color: %%DARK%%;
}

QSlider::groove:horizontal {
    height: 4px;
    background: #353535;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: %%DARK%%;
    border: none;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: %%DARK%%;
    border-radius: 2px;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1.5px solid #555555;
    background-color: #252525;
}
QCheckBox::indicator:hover {
    border-color: #888888;
}
QCheckBox::indicator:checked {
    background-color: %%DARK%%;
    border-color: %%DARK%%;
    image: url(assets/check.svg);
}

QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #404040;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical  { height: 0; }
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical  { background: transparent; }

QHeaderView::section {
    background-color: #242424;
    color: #888888;
    border: none;
    border-bottom: 1px solid #333333;
    border-right: 1px solid #2a2a2a;
    padding: 5px 8px;
    font-weight: 600;
    font-size: 12px;
}

/* Restore panel borders — native Fusion stops drawing StyledPanel frames once any stylesheet is active */
QFrame#sd_panel {
    border: 1px solid #3d3d3d;
    border-radius: 4px;
}

QTabWidget::pane { border: none; }

QTabBar#sub_tab_bar { background: transparent; }
QTabBar#sub_tab_bar::tab {
    background: transparent;
    color: #777777;
    border-radius: 5px;
    padding: 5px 16px;
    margin: 3px 2px;
    border: none;
}
QTabBar#sub_tab_bar::tab:selected {
    background: #2c2c2c;
    color: #d5d5d5;
}
QTabBar#sub_tab_bar::tab:hover:!selected {
    background: #252525;
    color: #aaaaaa;
}

QTabBar#main_tab_bar { background: #1c1c1c; }
QTabBar#main_tab_bar::tab {
    background: #1c1c1c;
    color: #757575;
    font-size: 14px;
    font-weight: 700;
    border: none;
    border-bottom: 3px solid transparent;
    padding: 13px 0 10px 0;
    margin: 0;
}
QTabBar#main_tab_bar::tab:selected {
    color: %%ACCENT%%;
    border-bottom: 3px solid %%ACCENT%%;
    background: #1e1e1e;
}
QTabBar#main_tab_bar::tab:hover:!selected {
    color: #b0b0b0;
    background: #202020;
    border-bottom: 3px solid #404040;
}

%%CENTRAL_BG%%
"""


def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linearly interpolate RGB between c1 (t=0) and c2 (t=1)."""
    r = int(c1.red()   + (c2.red()   - c1.red())   * t)
    g = int(c1.green() + (c2.green() - c1.green()) * t)
    b = int(c1.blue()  + (c2.blue()  - c1.blue())  * t)
    return QColor(r, g, b)


def _build_stylesheet(accent: str = "#f0a500", gradient: bool = True) -> str:
    dark = QColor(accent).darker(125).name()
    if gradient:
        muted = _blend(QColor(accent), QColor("#323232"), 0.55).name()
        central_bg = (
            f"QWidget#sd_central {{"
            f" background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f" stop:0 #0a0a0a, stop:0.5 #202020, stop:1 {muted}); }}"
        )
    else:
        central_bg = "QWidget#sd_central { background-color: #1c1c1c; }"
    return (
        _APP_STYLESHEET_TPL
        .replace("%%ACCENT%%", accent)
        .replace("%%DARK%%", dark)
        .replace("%%CENTRAL_BG%%", central_bg)
    )


def _apply_dark_theme(app: QApplication, accent: str = "#f0a500", gradient: bool = True) -> None:
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
    app.setStyleSheet(_build_stylesheet(accent, gradient))


class _UISignal(QObject):
    """Dispatch callables from worker threads to the main thread via signal."""
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(lambda fn: fn())


class _NoScrollSlider(QSlider):
    def wheelEvent(self, event) -> None:
        event.ignore()


class _MainTabBar(QTabBar):
    """Full-width proportional tab bar: last tab (Settings) gets ~25%, rest share ~75%."""

    def __init__(self) -> None:
        super().__init__()
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setObjectName("main_tab_bar")

    def _avail(self) -> int:
        p = self.parentWidget()
        w = (p.width() if p and p.width() > 0 else 0) or self.width()
        return w or 860

    def tabSizeHint(self, index: int) -> QSize:
        n = self.count()
        if n == 0:
            return super().tabSizeHint(index)
        total = self._avail()
        h     = super().tabSizeHint(index).height()
        if n == 1:
            return QSize(total, h)
        settings_w = max(150, total // 4)
        other_w    = (total - settings_w) // (n - 1)
        return QSize(settings_w if index == n - 1 else other_w, h)

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        n = self.count()
        if n < 2:
            return
        painter = QPainter(self)
        painter.setPen(QColor("#363636"))
        r = self.tabRect(n - 1)
        painter.drawLine(r.left(), 10, r.left(), self.height() - 10)
        painter.end()


class _MainTabWidget(QTabWidget):
    """QTabWidget backed by a full-width proportional tab bar."""

    def __init__(self) -> None:
        super().__init__()
        self.setTabBar(_MainTabBar())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.tabBar().updateGeometry()
        self.tabBar().update()



# ─────────────────────────────────────────────────────────────────────────────
# LIFX Effects tab
# ─────────────────────────────────────────────────────────────────────────────

class LIFXTab(QWidget):
    def __init__(
        self,
        engine: Engine,
        settings: dict,
        lights: list[dict],
        light_assignments: dict,
        on_change: Callable,
        on_force_restart: Callable,
        ui: _UISignal,
    ) -> None:
        super().__init__()
        self._engine           = engine
        self._on_change        = on_change
        self._on_force_restart = on_force_restart
        self._ui               = ui
        self._lights:            list[dict]       = list(lights)
        self._light_assignments: dict             = dict(light_assignments)
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
        self._section_count = 0
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
        left_f.setObjectName("sd_panel")
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

        self._lights_container = QWidget()
        container_layout = QVBoxLayout(self._lights_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        left_l.addWidget(self._lights_container)

        left_l.addStretch()
        self._lights_panel_layout = left_l
        body.addWidget(left_f)

        # ── Right: scrollable settings ─────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._scroll.setObjectName("sd_panel")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        right_w = QWidget()
        self._scroll_content = right_w
        right   = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 4, 8)
        right.setSpacing(0)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(right_w)
        body.addWidget(self._scroll, stretch=1)
        scroll = self._scroll  # keep local alias for rest of _build

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

        # FLAGS — per-flag enable/disable with colored indicators
        flag_hdr   = self._section(right, "FLAGS")
        flag_group = QWidget()
        flag_v     = QVBoxLayout(flag_group)
        flag_v.setContentsMargins(10, 0, 10, 4)
        flag_v.setSpacing(5)

        flags_enabled = s.get("flags_enabled", {})
        self._flag_checks: dict[str, QCheckBox] = {}
        for key in _FLAG_ORDER:
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(6)

            cb = QCheckBox()
            cb.setChecked(flags_enabled.get(key, True))
            row_h.addWidget(cb)

            dot = QLabel("●")
            dot.setStyleSheet(f"color: {_FLAG_DOT_COLOR[key]}; font-size: 16px;")
            row_h.addWidget(dot)

            name_lbl = QLabel(_FLAG_DISPLAY[key])
            name_lbl.setFixedWidth(110)
            row_h.addWidget(name_lbl)

            desc_lbl = QLabel(_FLAG_DESC[key])
            desc_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
            row_h.addWidget(desc_lbl)
            row_h.addStretch()

            flag_v.addWidget(row_w)
            self._flag_checks[key] = cb

        self._flag_bri = self._slider(flag_v, "Brightness %", s.get("flag_brightness_pct", 100), 0, 100)
        right.addWidget(flag_group)
        self._effect_groups["flag_effect"] = (flag_hdr, flag_group)

        # PIT LIMITER
        pit_hdr   = self._section(right, "PIT LIMITER")
        pit_group = QWidget()
        pit_v     = QVBoxLayout(pit_group)
        pit_v.setContentsMargins(10, 0, 10, 4)
        pit_v.setSpacing(3)
        self._pit_bri = self._slider(pit_v, "Brightness %", s.get("pit_limiter_brightness_pct", 75), 0, 100)
        right.addWidget(pit_group)
        self._effect_groups["pit_limiter"] = (pit_hdr, pit_group)

        right.addStretch()

        # Apply initial dimmed state
        for name, cb in self._effect_checks.items():
            if name in self._effect_groups:
                self._set_group_state(name, cb.isChecked())

        self._rebuild_lights_panel()

    def _section(self, layout: QVBoxLayout, text: str) -> QLabel:
        self._section_count += 1
        if self._section_count > 1:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Plain)
            sep.setStyleSheet(f"color: {_GREY};")
            layout.addWidget(sep)
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {_MUTED};"
            " padding-top: 10px; padding-bottom: 4px; padding-left: 10px;"
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

        val_edit = QLineEdit(str(value))
        val_edit.setFixedWidth(40)
        val_edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_edit.setValidator(QIntValidator(from_, to))
        val_edit.setStyleSheet(
            f"color: {_MUTED}; background: transparent; border: none;"
            f" border-bottom: 1px solid transparent;"
            f" padding: 0; margin: 0;"
        )
        row_h.addWidget(val_edit)

        # Slider → edit
        sl.valueChanged.connect(lambda v, ve=val_edit: ve.setText(str(v)))

        # Edit → slider on Enter or focus-out
        def _commit(ve=val_edit, s=sl):
            txt = ve.text().strip()
            try:
                v = max(s.minimum(), min(s.maximum(), int(txt)))
                s.setValue(v)
            except ValueError:
                ve.setText(str(s.value()))

        val_edit.editingFinished.connect(_commit)

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
        # Keep controls interactive so values can be pre-configured while the
        # effect is disabled; just dim the group opacity as a visual indicator.
        group.setEnabled(True)
        group.setGraphicsEffect(None)
        if not enabled:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(group)
            eff.setOpacity(0.45)
            group.setGraphicsEffect(eff)

    # ── wiring ────────────────────────────────────────────────────────────────

    def _wire_autosave(self) -> None:
        for sl in (self._start_rpm, self._redline, self._strip_bri,
                   self._brake_thr, self._brake_bri, self._flag_bri, self._pit_bri):
            sl.valueChanged.connect(lambda _: self._on_change())
        for cb in (self._mode_combo, self._scheme_combo, self._led_step_combo):
            cb.currentTextChanged.connect(lambda _: self._on_change())
        self._reversed_cb.stateChanged.connect(lambda _: self._on_change())
        for cb in self._flag_checks.values():
            cb.stateChanged.connect(lambda _: self._on_change())
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
            "rev_counter_lights":         self._light_assignments.get("rev_counter",  ["strip"]),
            "brake_lights":               self._light_assignments.get("brake_lights", []),
            "brake_threshold":            self._brake_thr.value() / 100.0,
            "brake_max_brightness":       self._brake_bri.value() / 100.0,
            "flag_lights":                self._light_assignments.get("flag_effect",  []),
            "flag_max_brightness":        self._flag_bri.value() / 100.0,
            "enabled_flags":              [k for k, cb in self._flag_checks.items() if cb.isChecked()],
            "pit_limiter_lights":         self._light_assignments.get("pit_limiter",  ["strip"]),
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
            "flags_enabled":              {k: cb.isChecked() for k, cb in self._flag_checks.items()},
            "pit_limiter_brightness_pct": self._pit_bri.value(),
        }

    def update_lights(self, lights: list[dict], assignments: dict) -> None:
        self._lights = list(lights)
        self._light_assignments = dict(assignments)
        self._rebuild_lights_panel()

    def _rebuild_lights_panel(self) -> None:
        self._light_dots = {}
        self._light_effect_labels = {}
        # Clear the container
        lay = self._lights_container.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Rebuild from self._lights
        for light in self._lights:
            name = light["name"]
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
            lay.addWidget(cell_w)
            self._light_dots[name] = dot
            self._light_effect_labels[name] = eff_lbl

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
            lbl.setText("\n".join(e.replace("_", " ") for e in effects_for))


# ─────────────────────────────────────────────────────────────────────────────
# Lights tab — light registry + effect assignments
# ─────────────────────────────────────────────────────────────────────────────

class _LightEditDialog(QDialog):
    """Add or edit a single LIFX light entry."""

    def __init__(self, parent=None, light: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Light" if light else "Add Light")
        self.setMinimumWidth(340)
        self.setModal(True)

        form = QVBoxLayout(self)
        form.setSpacing(10)

        def _row(label: str, widget) -> QHBoxLayout:
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(60)
            h.addWidget(lbl)
            h.addWidget(widget)
            return h

        self._name = QLineEdit(light.get("name", "") if light else "")
        self._name.setPlaceholderText("e.g. strip, front_right")
        form.addLayout(_row("Name", self._name))

        self._ip = QLineEdit(light.get("ip", "") if light else "")
        self._ip.setPlaceholderText("192.168.x.x")
        form.addLayout(_row("IP", self._ip))

        self._type = QComboBox()
        self._type.addItems(["LED Strip", "Bulb"])
        if light and light.get("type") == "single":
            self._type.setCurrentText("Bulb")
        form.addLayout(_row("Type", self._type))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        form.addWidget(btns)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #cc4444; font-size: 13px;")
        form.addWidget(self._status)

    def _validate(self) -> None:
        if not self._name.text().strip():
            self._status.setText("Name is required.")
            return
        if not self._ip.text().strip():
            self._status.setText("IP address is required.")
            return
        self.accept()

    def result_light(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "ip":   self._ip.text().strip(),
            "type": "multizone" if self._type.currentText() == "LED Strip" else "single",
        }


class _LightScanDialog(QDialog):
    """Discover LIFX lights on the LAN and add them to the registry."""

    _scan_done = Signal(list)

    def __init__(self, parent=None, existing_names: list[str] | None = None,
                 known_ips: list[str] | None = None) -> None:
        super().__init__(parent)
        self._scan_done.connect(self._finish_scan)
        self.setWindowTitle("Scan for LIFX Lights")
        self.setMinimumWidth(480)
        self.setMinimumHeight(360)
        self.setModal(True)

        self._existing_names = existing_names or []
        self._known_ips = known_ips or []
        self._discovered: list[dict] = []
        self._added: list[dict] = []

        v = QVBoxLayout(self)
        v.setSpacing(10)

        top_h = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Network")
        self._scan_btn.setFixedWidth(130)
        self._scan_btn.clicked.connect(self._start_scan)
        top_h.addWidget(self._scan_btn)
        self._scan_status = QLabel("Click Scan to discover LIFX lights on your network.")
        self._scan_status.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        self._scan_status.setWordWrap(True)
        top_h.addWidget(self._scan_status, stretch=1)
        v.addLayout(top_h)

        self._list = QListWidget()
        self._list.setMinimumHeight(160)
        self._list.itemSelectionChanged.connect(self._on_selection)
        v.addWidget(self._list)

        name_h = QHBoxLayout()
        name_h.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Choose a name for this light")
        name_h.addWidget(self._name_edit)
        v.addLayout(name_h)
        self._name_hint = QLabel("Select a discovered light above, then give it a name.")
        self._name_hint.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        v.addWidget(self._name_hint)

        btn_h = QHBoxLayout()
        self._add_btn = QPushButton("Add to Registry")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add_selected)
        btn_h.addWidget(self._add_btn)
        self._added_lbl = QLabel("")
        self._added_lbl.setStyleSheet(f"color: {_GREEN}; font-size: 13px;")
        btn_h.addWidget(self._added_lbl, stretch=1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_h.addWidget(close_btn)
        v.addLayout(btn_h)

    def _start_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._list.clear()
        self._discovered = []
        self._scan_status.setText("Scanning… (up to 10 seconds)")
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self) -> None:
        try:
            import lifxlan.lifxlan as _mod
            from lifxlan import LifxLAN, Light
            from lifxlan.msgtypes import GetService, StateService

            orig = _mod.UDP_BROADCAST_IP_ADDRS

            # Derive subnets from registry IPs so the sweep finds all lights on
            # the same /24, not just broadcast (which Windows Firewall blocks).
            subnets: set[str] = set()
            for ip in self._known_ips:
                parts = ip.split(".")
                if len(parts) == 4:
                    subnets.add(".".join(parts[:3]))

            # Fallback: try the local subnet detected by lifxlan
            if not subnets:
                for addr in orig:
                    parts = addr.split(".")
                    if len(parts) == 4 and parts[-1] == "255":
                        subnets.add(".".join(parts[:3]))

            # Phase 1: fast GetService sweep, batch 100 IPs at a time.
            # max_attempts=2 resends each batch so slower devices get a second
            # chance. This finds all devices without making per-device calls.
            found: dict[str, str] = {}  # mac -> ip
            for prefix in sorted(subnets):
                all_ips = [f"{prefix}.{i}" for i in range(1, 255)]
                batches = [all_ips[i:i + 100] for i in range(0, len(all_ips), 100)]
                for batch in batches:
                    _mod.UDP_BROADCAST_IP_ADDRS = batch
                    try:
                        lan = LifxLAN()
                        responses = lan.broadcast_with_resp(
                            GetService, StateService, timeout_secs=0.5, max_attempts=2
                        )
                        for r in responses:
                            if r.target_addr not in found:
                                found[r.target_addr] = r.ip_addr
                    except Exception:
                        pass
            _mod.UDP_BROADCAST_IP_ADDRS = orig

            # Phase 2: get label and type for each discovered device.
            result: list[dict] = []
            for mac, ip in sorted(found.items(), key=lambda x: tuple(int(p) for p in x[1].split("."))):
                try:
                    light = Light(mac, ip)
                    label = light.get_label() or ""
                    mz = light.supports_multizone()
                    result.append({"ip": ip, "label": label, "type": "multizone" if mz else "single"})
                except Exception:
                    result.append({"ip": ip, "label": "", "type": "single"})

            self._scan_status_text = f"Found {len(result)} light(s)." if result else "No lights found."
        except Exception as exc:
            result = []
            self._scan_status_text = f"Scan failed: {exc}"
        self._scan_done.emit(result)

    def _finish_scan(self, result: list) -> None:
        # Only show lights not already in the registry
        registered = set(self._known_ips)
        self._discovered = [d for d in result if d["ip"] not in registered]
        skipped = len(result) - len(self._discovered)
        status = getattr(self, "_scan_status_text", "")
        if skipped:
            status += f" ({skipped} already in registry, hidden)"
        self._scan_btn.setEnabled(True)
        self._scan_status.setText(status)
        self._list.clear()
        type_label = {"multizone": "Strip", "single": "Bulb"}
        for d in self._discovered:
            text = f"{d['label'] or d['ip']}  ·  {d['ip']}  ·  {type_label.get(d['type'], '')}"
            self._list.addItem(text)

    def _on_selection(self) -> None:
        items = self._list.selectedItems()
        if not items:
            self._add_btn.setEnabled(False)
            return
        idx = self._list.row(items[0])
        d = self._discovered[idx]
        if not self._name_edit.text():
            self._name_edit.setText(d.get("label", "").lower().replace(" ", "_") or "light")
        self._add_btn.setEnabled(True)

    def _add_selected(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        idx = self._list.row(items[0])
        d = self._discovered[idx]
        name = self._name_edit.text().strip()
        if not name:
            return
        self._added.append({"name": name, "ip": d["ip"], "type": d["type"]})
        self._added_lbl.setText(f"Added: {name}")
        self._name_edit.clear()
        self._list.clearSelection()
        self._add_btn.setEnabled(False)

    def get_added(self) -> list[dict]:
        return list(self._added)


_EFFECT_LABELS = [
    ("Rev Counter",  "rev_counter"),
    ("Brake Lights", "brake_lights"),
    ("Flag Effect",  "flag_effect"),
    ("Pit Limiter",  "pit_limiter"),
]


class LightsTab(QWidget):
    """Light registry (name/IP/type) and per-effect assignment checkboxes."""

    def __init__(self, settings: dict, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._on_change   = on_change
        self._lights:      list[dict] = list(settings.get("lights", []))
        self._assignments: dict       = {
            k: list(v) for k, v in settings.get("effect_lights", {
                "rev_counter": [], "brake_lights": [], "flag_effect": [], "pit_limiter": [],
            }).items()
        }
        self._status_dots:       dict[str, QLabel]    = {}
        self._assign_checks:     dict[str, dict[str, QCheckBox]] = {}  # effect -> {name -> cb}
        self._assign_section_layouts: dict[str, QVBoxLayout] = {}
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ── Left: registry ───────────────────────────────────────────────────
        left_f = QFrame()
        left_f.setFrameShape(QFrame.Shape.StyledPanel)
        left_f.setObjectName("sd_panel")
        left_f.setMinimumWidth(240)
        left_f.setMaximumWidth(320)
        left_v = QVBoxLayout(left_f)
        left_v.setContentsMargins(10, 10, 10, 10)
        left_v.setSpacing(6)

        reg_hdr = QLabel("REGISTRY")
        reg_hdr.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {_MUTED}; letter-spacing: 1px;")
        left_v.addWidget(reg_hdr)

        # Scrollable list of light rows
        self._registry_scroll = QScrollArea()
        self._registry_scroll.setWidgetResizable(True)
        self._registry_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._registry_content = QWidget()
        self._registry_layout  = QVBoxLayout(self._registry_content)
        self._registry_layout.setContentsMargins(0, 0, 0, 0)
        self._registry_layout.setSpacing(2)
        self._registry_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._registry_scroll.setWidget(self._registry_content)
        left_v.addWidget(self._registry_scroll, stretch=1)

        # Buttons
        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(4)
        self._add_btn  = QPushButton("+ Add")
        self._edit_btn = QPushButton("Edit")
        self._del_btn  = QPushButton("Remove")
        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn.clicked.connect(self._on_edit)
        self._del_btn.clicked.connect(self._on_remove)
        for b in (self._add_btn, self._edit_btn, self._del_btn):
            btn_row1.addWidget(b)
        left_v.addLayout(btn_row1)

        scan_btn = QPushButton("⊙  Scan Network")
        scan_btn.clicked.connect(self._on_scan)
        left_v.addWidget(scan_btn)

        outer.addWidget(left_f)

        # ── Right: assignments ───────────────────────────────────────────────
        right_f = QFrame()
        right_f.setFrameShape(QFrame.Shape.StyledPanel)
        right_f.setObjectName("sd_panel")
        right_v = QVBoxLayout(right_f)
        right_v.setContentsMargins(10, 10, 10, 10)
        right_v.setSpacing(6)

        asgn_hdr = QLabel("EFFECT ASSIGNMENTS")
        asgn_hdr.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {_MUTED}; letter-spacing: 1px;")
        right_v.addWidget(asgn_hdr)

        asgn_hint = QLabel("Choose which lights each effect controls.")
        asgn_hint.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        right_v.addWidget(asgn_hint)

        self._asgn_scroll = QScrollArea()
        self._asgn_scroll.setWidgetResizable(True)
        self._asgn_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._asgn_content = QWidget()
        self._asgn_layout  = QVBoxLayout(self._asgn_content)
        self._asgn_layout.setContentsMargins(0, 4, 0, 4)
        self._asgn_layout.setSpacing(0)
        self._asgn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._asgn_scroll.setWidget(self._asgn_content)
        right_v.addWidget(self._asgn_scroll, stretch=1)

        outer.addWidget(right_f, stretch=1)

        # Build initial rows
        self._rebuild_registry()
        self._rebuild_assignments()

    # ── registry ──────────────────────────────────────────────────────────────

    def _rebuild_registry(self) -> None:
        self._selected_name: str | None = None
        self._registry_rows: dict[str, QWidget] = {}
        self._status_dots = {}

        while self._registry_layout.count():
            item = self._registry_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for light in self._lights:
            self._add_registry_row(light)

    def _add_registry_row(self, light: dict) -> None:
        name = light["name"]
        row  = QWidget()
        row.setProperty("light_name", name)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row_h = QHBoxLayout(row)
        row_h.setContentsMargins(4, 4, 4, 4)
        row_h.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_GREY}; font-size: 16px;")
        row_h.addWidget(dot)

        info_v = QVBoxLayout()
        info_v.setSpacing(0)
        nm_lbl = QLabel(name)
        nm_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        info_v.addWidget(nm_lbl)
        ip_lbl = QLabel(light.get("ip", ""))
        ip_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        info_v.addWidget(ip_lbl)
        row_h.addLayout(info_v, stretch=1)

        type_badge = QLabel("Strip" if light.get("type") == "multizone" else "Bulb")
        type_badge.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        row_h.addWidget(type_badge)

        row.mousePressEvent = lambda ev, n=name: self._select_row(n)
        self._registry_layout.addWidget(row)
        self._registry_rows[name] = row
        self._status_dots[name]   = dot

    def _select_row(self, name: str) -> None:
        # Highlight selected row
        for n, row in self._registry_rows.items():
            row.setStyleSheet("background: #2a2a2a; border-radius: 4px;" if n == name else "")
        self._selected_name = name

    def _deselect_all(self) -> None:
        for row in self._registry_rows.values():
            row.setStyleSheet("")
        self._selected_name = None

    # ── assignments ───────────────────────────────────────────────────────────

    def _rebuild_assignments(self) -> None:
        self._assign_checks = {}
        self._assign_section_layouts = {}

        while self._asgn_layout.count():
            item = self._asgn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for label, key in _EFFECT_LABELS:
            # Section header
            hdr = QLabel(label.upper())
            hdr.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {_MUTED}; letter-spacing: 1px; margin-top: 10px;")
            self._asgn_layout.addWidget(hdr)

            # Container for checkboxes
            section_w = QWidget()
            section_v = QVBoxLayout(section_w)
            section_v.setContentsMargins(8, 2, 0, 4)
            section_v.setSpacing(2)
            self._asgn_layout.addWidget(section_w)
            self._assign_section_layouts[key] = section_v

            self._assign_checks[key] = {}
            assigned = self._assignments.get(key, [])
            if not self._lights:
                placeholder = QLabel("No lights configured yet.")
                placeholder.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
                section_v.addWidget(placeholder)
            else:
                for light in self._lights:
                    name = light["name"]
                    cb = QCheckBox(name)
                    cb.setChecked(name in assigned)
                    cb.toggled.connect(self._on_assignment_changed)
                    section_v.addWidget(cb)
                    self._assign_checks[key][name] = cb

    def _refresh_assignment_section(self, key: str) -> None:
        """Add/remove checkboxes for one effect section without full rebuild."""
        section_v = self._assign_section_layouts.get(key)
        if section_v is None:
            return
        # Clear
        while section_v.count():
            item = section_v.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._assign_checks[key] = {}
        assigned = self._assignments.get(key, [])
        if not self._lights:
            placeholder = QLabel("No lights configured yet.")
            placeholder.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
            section_v.addWidget(placeholder)
        else:
            for light in self._lights:
                name = light["name"]
                cb = QCheckBox(name)
                cb.setChecked(name in assigned)
                cb.toggled.connect(self._on_assignment_changed)
                section_v.addWidget(cb)
                self._assign_checks[key][name] = cb

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_assignment_changed(self) -> None:
        self._on_change()

    def _on_add(self, _=None) -> None:
        dlg = _LightEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        light = dlg.result_light()
        # Avoid duplicate names
        if any(l["name"] == light["name"] for l in self._lights):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Duplicate Name", f"A light named '{light['name']}' already exists.")
            return
        self._lights.append(light)
        self._add_registry_row(light)
        for key in self._assign_checks:
            self._refresh_assignment_section(key)
        self._on_change()

    def _on_edit(self, _=None) -> None:
        name = self._selected_name
        if not name:
            return
        light = next((l for l in self._lights if l["name"] == name), None)
        if not light:
            return
        dlg = _LightEditDialog(self, light)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_light()
        old_name = light["name"]
        # Update in place
        light.update(updated)
        # If name changed, update assignments and dots
        if updated["name"] != old_name:
            for key, assigned in self._assignments.items():
                if old_name in assigned:
                    assigned.remove(old_name)
                    assigned.append(updated["name"])
        self._rebuild_registry()
        self._rebuild_assignments()
        self._on_change()

    def _on_remove(self, _=None) -> None:
        name = self._selected_name
        if not name:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Remove Light",
            f"Remove '{name}' from the registry?\nIt will also be removed from any effect assignments.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._lights = [l for l in self._lights if l["name"] != name]
        for key, assigned in self._assignments.items():
            if name in assigned:
                assigned.remove(name)
        self._selected_name = None
        self._rebuild_registry()
        self._rebuild_assignments()
        self._on_change()

    def _on_scan(self, _=None) -> None:
        dlg = _LightScanDialog(self,
                               existing_names=[l["name"] for l in self._lights],
                               known_ips=[l["ip"] for l in self._lights])
        dlg.exec()
        added = dlg.get_added()
        if not added:
            return
        for light in added:
            if not any(l["name"] == light["name"] for l in self._lights):
                self._lights.append(light)
                self._add_registry_row(light)
        if added:
            for key in self._assign_checks:
                self._refresh_assignment_section(key)
            self._on_change()

    # ── public ────────────────────────────────────────────────────────────────

    def get_lights(self) -> list[dict]:
        return list(self._lights)

    def get_assignments(self) -> dict:
        result = {}
        for effect, checks in self._assign_checks.items():
            result[effect] = [name for name, cb in checks.items() if cb.isChecked()]
        # For effects that have no checkboxes yet (no lights), preserve existing
        for key in ("rev_counter", "brake_lights", "flag_effect", "pit_limiter"):
            if key not in result:
                result[key] = list(self._assignments.get(key, []))
        return result

    def get_settings(self) -> dict:
        return {
            "lights":       self.get_lights(),
            "effect_lights": self.get_assignments(),
        }

    def update_light_status(self, status: dict) -> None:
        for name, dot in self._status_dots.items():
            s = status.get(name, "idle")
            dot.setStyleSheet(f"color: {_dot_color(s)}; font-size: 16px;")


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

        from test_harness import TestPanel
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
        self._auto_cb.setChecked(True)
        self._auto_cb.stateChanged.connect(self._on_auto_changed)
        bar_h.addWidget(self._auto_cb)
        self._toggle_btn = QPushButton("Start Recording")
        self._toggle_btn.setFixedWidth(140)
        self._toggle_btn.clicked.connect(self._toggle)
        bar_h.addWidget(self._toggle_btn)
        root.addWidget(bar_w)

        # Vehicle / track subtitle
        self._session_meta_lbl = QLabel("")
        self._session_meta_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 16px; padding-left: 2px;")
        root.addWidget(self._session_meta_lbl)

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
            self._logger.update_session_game(game)
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
        vehicle     = telemetry.get("vehicle", "") or ""
        track       = telemetry.get("track",   "") or ""

        def _readable(s: str) -> bool:
            # suppress internal codes like "DT__260712040345" (all-caps/digits/underscores)
            return bool(s) and not all(c.isupper() or c.isdigit() or c in "_- " for c in s)
        meta_parts = [p for p in [vehicle, track] if _readable(p)]
        self._session_meta_lbl.setText("  ·  ".join(meta_parts))

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
# Settings tab
# ─────────────────────────────────────────────────────────────────────────────

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

        theme_row = QHBoxLayout()
        theme_row.setSpacing(10)
        theme_lbl = QLabel("Style")
        theme_lbl.setFixedWidth(130)
        theme_row.addWidget(theme_lbl)
        theme_combo = QComboBox()
        theme_combo.addItem("Mirrored (fill)",  "mirrored")
        theme_combo.addItem("Lines (baseline)", "lines")
        saved_theme = settings.get("overlay_theme", "mirrored")
        theme_combo.setCurrentIndex(0 if saved_theme == "mirrored" else 1)
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

    # ── helpers ───────────────────────────────────────────────────────────────

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

    # ── slots ─────────────────────────────────────────────────────────────────

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
        """Called on the main thread after a manual update check."""
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
        """Called when the background startup check finds a newer version."""
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
        self._update_status_lbl.setStyleSheet(f"color: #cc4444; font-size: 14px;")
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


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry overlay
# ─────────────────────────────────────────────────────────────────────────────

_OVL_W        = 320
_OVL_H        = 80
_OVL_FPS      = 60
_OVL_HISTORY  = 600   # ~10s at 60Hz


class TelemetryOverlay(QWidget):
    """Frameless always-on-top window with two rendering themes."""

    THEME_MIRRORED = "mirrored"   # throttle above / brake below center line, filled
    THEME_LINES    = "lines"      # both lines rise from baseline, no fill

    def __init__(self, engine: "Engine", moza: "MozaPedals | None" = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._engine     = engine
        self._moza       = moza
        self._throttle: deque = deque([0.0] * _OVL_HISTORY, maxlen=_OVL_HISTORY)
        self._brake:    deque = deque([0.0] * _OVL_HISTORY, maxlen=_OVL_HISTORY)
        self._clutch:   deque = deque([0.0] * _OVL_HISTORY, maxlen=_OVL_HISTORY)
        self._bg_alpha   = 0.70
        self._line_alpha = 1.0
        self._scale      = 100
        self._theme      = self.THEME_MIRRORED
        self._drag_pos   = None
        self._apply_size()

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // _OVL_FPS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _apply_size(self) -> None:
        self.setFixedSize(max(80, int(_OVL_W * self._scale / 100)),
                          max(30, int(_OVL_H * self._scale / 100)))

    def set_bg_alpha(self, alpha: float) -> None:
        self._bg_alpha = max(0.0, min(1.0, alpha))
        self.update()

    def set_line_alpha(self, alpha: float) -> None:
        self._line_alpha = max(0.0, min(1.0, alpha))
        self.update()

    def set_scale(self, pct: int) -> None:
        self._scale = max(50, min(200, pct))
        self._apply_size()
        self.update()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def _tick(self) -> None:
        if self._moza and self._moza.connected:
            thr = self._moza.throttle
            brk = self._moza.brake
            clu = self._moza.clutch
        else:
            tel = self._engine.get_telemetry()
            thr = max(0.0, min(1.0, float(tel.get("throttle", 0.0)) / 100.0))
            brk = max(0.0, min(1.0, float(tel.get("brake",    0.0)) / 100.0))
            clu = max(0.0, min(1.0, float(tel.get("clutch",   0.0)) / 100.0))
        self._throttle.append(thr)
        self._brake.append(brk)
        self._clutch.append(clu)
        self.update()

    def paintEvent(self, _) -> None:
        from PySide6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h  = self.width(), self.height()
        pad_x = 10
        pad_y = 8
        gw    = w - pad_x * 2
        gh    = h - pad_y * 2
        line_w = max(1.0, 1.5 * self._scale / 100.0)

        # Background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(12, 12, 12, int(220 * self._bg_alpha))))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)

        n   = _OVL_HISTORY
        thr = list(self._throttle)
        brk = list(self._brake)
        clu = list(self._clutch)

        def xp(i: int) -> float:
            return pad_x + i * gw / max(n - 1, 1)

        if self._theme == self.THEME_MIRRORED:
            self._paint_mirrored(painter, thr, brk, clu, xp, pad_x, pad_y, gw, gh, line_w)
        else:
            self._paint_lines(painter, thr, brk, clu, xp, pad_x, pad_y, gw, gh, line_w)

        painter.end()

    @staticmethod
    def _smooth(pts: list[tuple[float, float]]) -> QPainterPath:
        """Quadratic bezier through midpoints — smooths staircase artefacts from low-rate data."""
        path = QPainterPath()
        n = len(pts)
        if n == 0:
            return path
        path.moveTo(pts[0][0], pts[0][1])
        if n == 1:
            return path
        # Walk through midpoints as bezier targets; actual data points are control points
        mx, my = (pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0
        path.lineTo(mx, my)
        for i in range(1, n - 1):
            cx, cy = pts[i]
            mx = (pts[i][0] + pts[i + 1][0]) / 2.0
            my = (pts[i][1] + pts[i + 1][1]) / 2.0
            path.quadTo(cx, cy, mx, my)
        path.lineTo(pts[-1][0], pts[-1][1])
        return path

    def _paint_mirrored(self, painter, thr, brk, clu, xp, pad_x, pad_y, gw, gh, line_w) -> None:
        """Throttle fills upward from center; brake fills downward from center; clutch line above center."""
        cy     = pad_y + gh / 2.0
        half_h = gh / 2.0 - 1
        n      = _OVL_HISTORY

        painter.setPen(QPen(QColor(70, 70, 70, int(180 * self._bg_alpha)), 1.0))
        painter.drawLine(pad_x, int(cy), pad_x + gw, int(cy))

        t_pts = [(xp(i), cy - v * half_h) for i, v in enumerate(thr)]
        b_pts = [(xp(i), cy + v * half_h) for i, v in enumerate(brk)]
        c_pts = [(xp(i), cy - v * half_h) for i, v in enumerate(clu)]

        # Fills (straight lineTo is fine — interior not visible)
        t_fill = QPainterPath()
        t_fill.moveTo(xp(0), cy)
        for x, y in t_pts:
            t_fill.lineTo(x, y)
        t_fill.lineTo(xp(n - 1), cy)
        t_fill.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(t_fill, QBrush(QColor(0x2e, 0xcc, 0x71, int(120 * self._line_alpha))))

        b_fill = QPainterPath()
        b_fill.moveTo(xp(0), cy)
        for x, y in b_pts:
            b_fill.lineTo(x, y)
        b_fill.lineTo(xp(n - 1), cy)
        b_fill.closeSubpath()
        painter.fillPath(b_fill, QBrush(QColor(0xe7, 0x4c, 0x3c, int(120 * self._line_alpha))))

        # Smooth edge lines
        painter.strokePath(self._smooth(t_pts),
                           QPen(QColor(0x2e, 0xcc, 0x71, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(b_pts),
                           QPen(QColor(0xe7, 0x4c, 0x3c, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(c_pts),
                           QPen(QColor(0x3a, 0x9b, 0xdc, int(200 * self._line_alpha)), line_w))

    def _paint_lines(self, painter, thr, brk, clu, xp, pad_x, pad_y, gw, gh, line_w) -> None:
        """Throttle, brake, and clutch all rise from the same baseline, lines only."""
        base_y = float(pad_y + gh)

        painter.setPen(QPen(QColor(70, 70, 70, int(160 * self._bg_alpha)), 1.0))
        painter.drawLine(pad_x, int(base_y), pad_x + gw, int(base_y))

        t_pts = [(xp(i), base_y - v * gh) for i, v in enumerate(thr)]
        b_pts = [(xp(i), base_y - v * gh) for i, v in enumerate(brk)]
        c_pts = [(xp(i), base_y - v * gh) for i, v in enumerate(clu)]

        painter.strokePath(self._smooth(t_pts),
                           QPen(QColor(0x2e, 0xcc, 0x71, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(b_pts),
                           QPen(QColor(0xe7, 0x4c, 0x3c, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(c_pts),
                           QPen(QColor(0x3a, 0x9b, 0xdc, int(200 * self._line_alpha)), line_w))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


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

        # Apply saved font before any widgets are created
        _apply_font_size(settings.get("font_size_pt", 10))

        # Non-tab settings kept in sync with settings.json
        self._app_settings: dict = {
            "font_size_pt":      settings.get("font_size_pt",      10),
            "accent_color":      settings.get("accent_color",      "#f0a500"),
            "start_minimized":   settings.get("start_minimized",   False),
            "simhub_host":       settings.get("simhub_host",       config.SIMHUB_HOST),
            "simhub_port":       settings.get("simhub_port",       config.SIMHUB_PORT),
            "update_dot_pulse":  settings.get("update_dot_pulse",  True),
            "overlay_visible":      settings.get("overlay_visible",      False),
            "overlay_theme":        settings.get("overlay_theme",        "mirrored"),
            "overlay_bg_opacity":   settings.get("overlay_bg_opacity",   70),
            "overlay_line_opacity": settings.get("overlay_line_opacity", 100),
            "overlay_scale":        settings.get("overlay_scale",        100),
            "overlay_x":            settings.get("overlay_x",            None),
            "overlay_y":            settings.get("overlay_y",            None),
            "gradient_bg":          settings.get("gradient_bg",          True),
        }

        lights     = settings.get("lights", [])
        effect_lights = settings.get("effect_lights", {
            "rev_counter": [], "brake_lights": [], "flag_effect": [], "pit_limiter": [],
        })

        # One-time migration: seed registry from config_local.LIFX_LIGHTS
        if not lights:
            try:
                import config_local  # type: ignore
                legacy = getattr(config_local, "LIFX_LIGHTS", {})
                if legacy:
                    lights = [
                        {"name": name, "ip": cfg["ip"],
                         "type": "multizone" if "strip" in name else "single"}
                        for name, cfg in legacy.items()
                    ]
                    settings["lights"] = lights
                    settings_manager.save(settings)
            except ImportError:
                pass

        lights_config = {l["name"]: {"ip": l["ip"]} for l in lights}

        self._logger   = TelemetryLogger()
        self._engine   = Engine(
            simhub_host=self._app_settings["simhub_host"],
            simhub_port=self._app_settings["simhub_port"],
            lights_config=lights_config,
        )
        self._splitter = UDPSplitter(
            listen_port=settings["splitter_port"],
            targets=[(t["ip"], t["port"]) for t in settings["splitter_targets"]],
        )

        # Central widget
        central = QWidget()
        central.setObjectName("sd_central")
        self.setCentralWidget(central)
        main_v = QVBoxLayout(central)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        main_tabs = _MainTabWidget()
        self._main_tabs = main_tabs
        main_v.addWidget(main_tabs, stretch=1)

        # ── Light Control ──────────────────────────────────────────────────
        light_tabs = QTabWidget()
        light_tabs.tabBar().setObjectName("sub_tab_bar")
        light_tabs.tabBar().setDrawBase(False)

        self._lifx_tab = LIFXTab(
            engine=self._engine,
            settings=settings,
            lights=lights,
            light_assignments=effect_lights,
            on_change=self._on_lifx_change,
            on_force_restart=self._force_restart,
            ui=self._ui,
        )
        light_tabs.addTab(self._lifx_tab, "LIFX Effects")

        self._lights_tab = LightsTab(
            settings=settings,
            on_change=self._on_lights_change,
        )
        light_tabs.addTab(self._lights_tab, "Lights")

        self._splitter_tab = SplitterTab(self._splitter, settings, self._save_settings)
        light_tabs.addTab(self._splitter_tab, "UDP Splitter")

        self._test_tab = TestTab(self._engine, self._lifx_tab.get_effect_kwargs, self._ui)
        light_tabs.addTab(self._test_tab, "Test")

        main_tabs.addTab(light_tabs, "Light Control")

        # ── Lap Logs ───────────────────────────────────────────────────────
        lap_tabs = QTabWidget()
        lap_tabs.tabBar().setObjectName("sub_tab_bar")
        lap_tabs.tabBar().setDrawBase(False)

        self._logger_tab = LoggerTab(self._logger, self._ui)
        lap_tabs.addTab(self._logger_tab, "Logger")

        self._history_tab = HistoryTab(self._logger)
        lap_tabs.addTab(self._history_tab, "History")

        main_tabs.addTab(lap_tabs, "Lap Logs")

        # ── Settings ────────────────��──────────────────────────────────────
        self._settings_tab = SettingsTab(
            settings=settings,
            on_font_change=self._on_font_change_setting,
            on_check_update=self._manual_update_check,
            on_startup_change=self._on_startup_change,
            on_simhub_change=self._on_simhub_change,
            on_accent_change=self._on_accent_change,
            on_install_update=self._do_install_update,
            on_pulse_change=self._on_pulse_change,
            on_overlay_change=self._on_overlay_show_change,
            on_overlay_theme_change=self._on_overlay_theme_change,
            on_overlay_bg_opacity_change=self._on_overlay_bg_opacity_change,
            on_overlay_line_opacity_change=self._on_overlay_line_opacity_change,
            on_overlay_scale_change=self._on_overlay_scale_change,
            on_gradient_change=self._on_gradient_change,
        )
        main_tabs.addTab(self._settings_tab, "Settings")

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
        self._check_game()  # detect game immediately so recording starts with the right name

        self._last_tray_status: str | None = None
        self._update_version: str | None = None
        self._update_download_url: str = ""
        self._setup_tray()

        initial_kwargs = self._lifx_tab.get_effect_kwargs()
        self._engine.start(initial_kwargs)
        self._splitter.start()

        # Moza pedal reader (high-rate hardware input, falls back to SimHub if unavailable)
        self._moza = MozaPedals()
        self._moza.start()

        # Telemetry overlay
        self._overlay = TelemetryOverlay(self._engine, self._moza)
        self._overlay.set_theme(     self._app_settings["overlay_theme"])
        self._overlay.set_bg_alpha(  self._app_settings["overlay_bg_opacity"]   / 100.0)
        self._overlay.set_line_alpha(self._app_settings["overlay_line_opacity"]  / 100.0)
        self._overlay.set_scale(     self._app_settings["overlay_scale"])
        ox = self._app_settings.get("overlay_x")
        oy = self._app_settings.get("overlay_y")
        if ox is not None and oy is not None:
            self._overlay.move(ox, oy)
        if self._app_settings.get("overlay_visible", False):
            self._overlay.show()

        threading.Thread(target=self._bg_update_check, daemon=True).start()

        QTimer(self).singleShot(0, self._fit_to_content)

    # ── sizing ────────────────────────────────────────────────────────────────

    def _fit_to_content(self) -> None:
        """Expand the window height so the LIFX scroll area needs no scrollbar."""
        content_h  = self._lifx_tab._scroll_content.sizeHint().height()
        viewport_h = self._lifx_tab._scroll.viewport().height()
        deficit    = content_h - viewport_h
        if deficit > 0:
            screen_h = QApplication.primaryScreen().availableGeometry().height()
            new_h    = min(self.height() + deficit + 16, screen_h - 60)
            self.resize(self.width(), new_h)

    # ── settings ──────────────────────────────────────────────────────────────

    def _save_settings(self) -> None:
        settings = {}
        settings.update(self._lifx_tab.get_settings())
        settings.update(self._splitter_tab.get_settings())
        settings.update(self._lights_tab.get_settings())
        settings.update(self._app_settings)
        settings_manager.save(settings)

    def _on_font_change_setting(self, size_pt: int) -> None:
        _apply_font_size(size_pt)
        self._app_settings["font_size_pt"] = size_pt
        self._save_settings()

    def _refresh_theme_stylesheet(self) -> None:
        QApplication.instance().setStyleSheet(
            _build_stylesheet(self._app_settings["accent_color"],
                              self._app_settings["gradient_bg"])
        )

    def _on_accent_change(self, color: str) -> None:
        self._app_settings["accent_color"] = color
        self._refresh_theme_stylesheet()
        self._save_settings()

    def _on_gradient_change(self, enabled: bool) -> None:
        self._app_settings["gradient_bg"] = enabled
        self._refresh_theme_stylesheet()
        self._save_settings()

    def _on_startup_change(self, launch: bool, minimized: bool) -> None:
        _set_startup_registry(launch)
        self._app_settings["start_minimized"] = minimized
        self._save_settings()

    def _on_simhub_change(self, host: str, port: int) -> None:
        self._app_settings["simhub_host"] = host
        self._app_settings["simhub_port"] = port
        self._save_settings()
        self._engine.set_simhub_address(host, port)
        self._restart_timer.stop()
        self._lifx_tab.mark_pending(False)
        self._do_restart()

    def _on_lifx_change(self) -> None:
        self._save_settings()
        self._lifx_tab.mark_pending(True)
        self._restart_timer.start()

    def _on_lights_change(self) -> None:
        lights    = self._lights_tab.get_lights()
        asgn      = self._lights_tab.get_assignments()
        lc        = {l["name"]: {"ip": l["ip"]} for l in lights}
        self._engine.update_lights_config(lc)
        self._lifx_tab.update_lights(lights, asgn)
        self._save_settings()
        self._force_restart()

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
        self._lights_tab.update_light_status(status.get("lights", {}))
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

        self._settings_tab.set_moza_status(self._moza.connected, self._moza.port)

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

    def _show_update_dot(self) -> None:
        from PySide6.QtCore import QPropertyAnimation, QSequentialAnimationGroup, QEasingCurve
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet("background: #2ecc71; border-radius: 4px; margin-left: 5px; margin-right: 2px;")
        dot.setToolTip("Update available — go to Settings")
        self._update_dot = dot

        effect = QGraphicsOpacityEffect(dot)
        dot.setGraphicsEffect(effect)
        self._update_dot_effect = effect

        fade_out = QPropertyAnimation(effect, b"opacity", dot)
        fade_out.setDuration(900)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.2)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutSine)
        fade_in = QPropertyAnimation(effect, b"opacity", dot)
        fade_in.setDuration(900)
        fade_in.setStartValue(0.2)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim = QSequentialAnimationGroup(dot)
        anim.addAnimation(fade_out)
        anim.addAnimation(fade_in)
        anim.setLoopCount(-1)
        self._update_dot_anim = anim

        if self._app_settings.get("update_dot_pulse", True):
            anim.start()

        settings_idx = self._main_tabs.count() - 1
        self._main_tabs.tabBar().setTabButton(settings_idx, QTabBar.ButtonPosition.RightSide, dot)

    def _on_overlay_theme_change(self, theme: str) -> None:
        self._app_settings["overlay_theme"] = theme
        self._save_settings()
        self._overlay.set_theme(theme)

    def _on_overlay_show_change(self, visible: bool) -> None:
        self._app_settings["overlay_visible"] = visible
        self._save_settings()
        if visible:
            self._overlay.show()
        else:
            self._overlay.hide()

    def _on_overlay_bg_opacity_change(self, pct: int) -> None:
        self._app_settings["overlay_bg_opacity"] = pct
        self._save_settings()
        self._overlay.set_bg_alpha(pct / 100.0)

    def _on_overlay_line_opacity_change(self, pct: int) -> None:
        self._app_settings["overlay_line_opacity"] = pct
        self._save_settings()
        self._overlay.set_line_alpha(pct / 100.0)

    def _on_overlay_scale_change(self, pct: int) -> None:
        self._app_settings["overlay_scale"] = pct
        self._save_settings()
        self._overlay.set_scale(pct)

    def _on_pulse_change(self, enabled: bool) -> None:
        self._app_settings["update_dot_pulse"] = enabled
        self._save_settings()
        if not hasattr(self, "_update_dot_anim"):
            return
        if enabled:
            self._update_dot_anim.start()
        else:
            self._update_dot_anim.stop()
            self._update_dot_effect.setOpacity(1.0)

    def _bg_update_check(self) -> None:
        result = _check_for_update()
        if result:
            ver, url = result
            self._update_version = ver
            self._update_download_url = url
            self._ui.call.emit(lambda: self._tray.update_menu())
            self._ui.call.emit(lambda: self._settings_tab.set_update_available(ver, url))
            self._ui.call.emit(self._show_update_dot)

    def _manual_update_check(self) -> None:
        def _worker() -> None:
            result = _check_for_update()
            if result:
                ver, url = result
                self._update_version = ver
                self._update_download_url = url
                self._ui.call.emit(lambda: self._tray.update_menu())
                try:
                    self._tray.notify(f"Update available: v{ver} — click tray to install.", "SimDeck")
                except Exception:
                    pass
                self._ui.call.emit(lambda: self._settings_tab.set_update_result(ver, url))
                self._ui.call.emit(self._show_update_dot)
            else:
                self._ui.call.emit(lambda: self._settings_tab.set_update_result(None))
        threading.Thread(target=_worker, daemon=True).start()

    def _do_install_update(self, url: str) -> None:
        """Download the installer to %TEMP%, launch it, then quit the app."""
        import tempfile
        from pathlib import Path
        self._ui.call.emit(lambda: self._settings_tab.set_downloading())

        def _worker() -> None:
            try:
                tmp = Path(tempfile.gettempdir()) / "simdeck-update.exe"
                urllib.request.urlretrieve(url, tmp)
                self._ui.call.emit(lambda: self._launch_installer_and_quit(str(tmp)))
            except Exception:
                self._ui.call.emit(lambda: self._settings_tab.set_download_error())

        threading.Thread(target=_worker, daemon=True).start()

    def _launch_installer_and_quit(self, path: str) -> None:
        import os
        os.startfile(path)
        self._quit()

    def _setup_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Open", lambda *_: self._ui.call.emit(self._restore), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: f"Update available: v{self._update_version} — click to install",
                lambda *_: self._ui.call.emit(
                    lambda: self._do_install_update(self._update_download_url or _RELEASES_PAGE)
                ),
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
        pos = self._overlay.pos()
        self._app_settings["overlay_x"] = pos.x()
        self._app_settings["overlay_y"] = pos.y()
        self._save_settings()

        # Stop UI timers and remove overlay from screen immediately
        self._poll_timer.stop()
        self._game_timer.stop()
        self._overlay._timer.stop()
        self._overlay.hide()

        # Signal background threads to stop — no join needed, all are daemon=True
        # and will die when the process exits; joining on the main thread risks
        # blocking for seconds if a LIFX/SimHub socket call is in progress.
        self._moza.stop()
        self._engine._stop_event.set()
        self._splitter._stop_event.set()
        if self._tray:
            self._tray.stop()

        os._exit(0)

    @property
    def start_minimized(self) -> bool:
        return bool(self._app_settings.get("start_minimized", False))

    def closeEvent(self, event) -> None:
        self._save_settings()
        event.ignore()
        self._to_tray()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _init_settings = settings_manager.load()
    _init_accent   = _init_settings.get("accent_color", "#f0a500")
    _init_gradient = _init_settings.get("gradient_bg",  True)
    _apply_dark_theme(app, _init_accent, _init_gradient)
    window = SimDeckApp()
    if not window.start_minimized:
        window.show()
    sys.exit(app.exec())
