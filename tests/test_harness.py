"""SimDeck Test Harness — per-effect interactive testing."""

import sys
import threading
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QButtonGroup,
    QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QCheckBox, QPushButton, QLineEdit, QFrame,
)

import config
import log_setup
import settings_manager

log_setup.setup()
from effects import EFFECTS
from lifx_controller import LIFXController
from light_rig import LightRig

_GREEN  = "#2ecc71"
_YELLOW = "#f0a500"
_MUTED  = "#888888"
_RED    = "#e74c3c"

POLL_HZ       = 20
POLL_INTERVAL = 1 / POLL_HZ
RAMP_SECONDS  = 4.0

_FLAGS = [
    ("None",      None,             "#555555"),
    ("Yellow",    "flag_yellow",    "#f0c030"),
    ("Red",       "flag_red",       "#e74c3c"),
    ("Blue",      "flag_blue",      "#3498db"),
    ("White",     "flag_white",     "#dddddd"),
    ("Green",     "flag_green",     "#2ecc71"),
    ("Checkered", "flag_checkered", "#aaaaaa"),
    ("Black",     "flag_black",     "#777777"),
]

_REV_MODES = [
    ("Center",      "rev_counter", "center"),
    ("Left / Right","rev_counter", "left_right"),
    ("Full Fill",   "rev_counter", "full"),
    ("Rev Lights",  "rev_lights",  "left_right"),
]


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
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(100, 100, 100))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(100, 100, 100))
    app.setPalette(p)


class _UISignal(QObject):
    call = Signal(object)
    def __init__(self) -> None:
        super().__init__()
        self.call.connect(lambda fn: fn())


class _NoScrollSlider(QSlider):
    def wheelEvent(self, event) -> None:
        event.ignore()


class TestPanel(QWidget):
    """Embeddable per-effect test panel. Call connect() in a background thread after adding to a layout."""

    def __init__(self, parent: QWidget, ui: "_UISignal | None" = None,
                 get_effect_kwargs: "Callable | None" = None) -> None:
        super().__init__(parent)
        self._ui                = ui
        self._get_effect_kwargs = get_effect_kwargs
        self._effects: list     = []
        self._rig               = None
        self._connected         = False
        self._shared_rig        = False
        self._ramp_rpm          = False
        self._ramp_brake        = False
        self._lock              = threading.Lock()
        self._telemetry: dict   = {
            "rpm": 0.0, "max_rpm": 8500.0, "brake": 0.0,
            "flag_yellow": 0.0, "flag_red": 0.0, "flag_blue": 0.0,
            "flag_white": 0.0, "flag_green": 0.0, "flag_checkered": 0.0,
            "flag_black": 0.0, "pit_limiter": 0.0,
        }
        self._settings: dict    = {}
        self._poll_timer        = QTimer(self)
        self._poll_timer.setInterval(int(POLL_INTERVAL * 1000))
        self._poll_timer.timeout.connect(self._poll)
        self._build()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # Status row
        st_w = QWidget()
        st_h = QHBoxLayout(st_w)
        st_h.setContentsMargins(0, 0, 0, 0)
        self._status_lbl = QLabel("Not connected")
        self._status_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        st_h.addWidget(self._status_lbl)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {_MUTED}; font-size: 16px;")
        st_h.addWidget(self._status_dot)
        st_h.addStretch()
        root.addWidget(st_w)

        # Per-effect panels in a tab widget
        self._tabs = QTabWidget()
        self._tabs.tabBar().setObjectName("sub_tab_bar")
        self._tabs.tabBar().setDrawBase(False)
        self._tabs.addTab(self._build_rev_panel(),   "Rev Counter")
        self._tabs.addTab(self._build_brake_panel(), "Brake Lights")
        self._tabs.addTab(self._build_flags_panel(), "Flags")
        self._tabs.addTab(self._build_pit_panel(),   "Pit Limiter")
        root.addWidget(self._tabs, stretch=1)

    # ── Rev Counter panel ─────────────────────────────────────────────────────

    def _build_rev_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 8)
        v.setSpacing(8)

        # Max RPM + full range
        cfg_w = QWidget()
        cfg_h = QHBoxLayout(cfg_w)
        cfg_h.setContentsMargins(0, 0, 0, 0)
        cfg_h.setSpacing(6)
        cfg_h.addWidget(QLabel("Max RPM"))
        self._max_rpm_entry = QLineEdit("8500")
        self._max_rpm_entry.setFixedWidth(80)
        cfg_h.addWidget(self._max_rpm_entry)
        set_btn = QPushButton("Set")
        set_btn.setFixedWidth(50)
        set_btn.clicked.connect(self._set_max_rpm)
        cfg_h.addWidget(set_btn)
        cfg_h.addStretch()
        self._full_range_cb = QCheckBox("Full range")
        self._full_range_cb.setChecked(True)
        self._full_range_cb.stateChanged.connect(lambda _: self._rebuild_effects())
        cfg_h.addWidget(self._full_range_cb)
        v.addWidget(cfg_w)

        # Animation mode
        mode_w = QWidget()
        mode_h = QHBoxLayout(mode_w)
        mode_h.setContentsMargins(0, 0, 0, 0)
        mode_h.setSpacing(6)
        mode_h.addWidget(QLabel("Mode"))
        self._rev_mode_group = QButtonGroup(self)
        self._rev_mode_group.setExclusive(True)
        self._rev_mode_btns: list[QPushButton] = []
        for i, (label, _eff, _mode) in enumerate(_REV_MODES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            self._rev_mode_group.addButton(btn, i)
            mode_h.addWidget(btn)
            self._rev_mode_btns.append(btn)
        self._rev_mode_group.idClicked.connect(lambda _: self._rebuild_effects())
        mode_h.addStretch()
        v.addWidget(mode_w)

        # RPM slider
        self._rpm_slider, self._rpm_lbl = self._make_slider(v, "RPM", 0, 8500)
        self._rpm_slider.valueChanged.connect(lambda val: self._set_tel("rpm", float(val)))

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        v.addWidget(sep)

        # Ramp + snap buttons
        act_w = QWidget()
        act_h = QHBoxLayout(act_w)
        act_h.setContentsMargins(0, 0, 0, 0)
        act_h.setSpacing(6)
        self._ramp_btn = QPushButton("▶  Ramp RPM")
        self._ramp_btn.clicked.connect(self._toggle_ramp)
        act_h.addWidget(self._ramp_btn)
        for label, frac in (("Idle", 0.0), ("Mid", 0.65), ("Redline", 0.98)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, f=frac: self._snap_rpm(f))
            act_h.addWidget(btn)
        act_h.addStretch()
        v.addWidget(act_w)

        v.addStretch()
        return w

    # ── Brake Lights panel ────────────────────────────────────────────────────

    def _build_brake_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 8)
        v.setSpacing(8)

        self._brake_slider, self._brake_lbl = self._make_slider(v, "Brake %", 0, 100)
        self._brake_slider.valueChanged.connect(lambda val: self._set_tel("brake", float(val)))

        act_w = QWidget()
        act_h = QHBoxLayout(act_w)
        act_h.setContentsMargins(0, 0, 0, 0)
        act_h.setSpacing(6)
        self._ramp_brake_btn = QPushButton("▶  Ramp Brake")
        self._ramp_brake_btn.clicked.connect(self._toggle_ramp_brake)
        act_h.addWidget(self._ramp_brake_btn)
        rel_btn = QPushButton("Release")
        rel_btn.clicked.connect(lambda: self._set_tel_ui("brake", 0.0))
        act_h.addWidget(rel_btn)
        act_h.addStretch()
        v.addWidget(act_w)

        v.addStretch()
        return w

    # ── Flags panel ───────────────────────────────────────────────────────────

    def _build_flags_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 8)
        v.setSpacing(12)

        note = QLabel("Click a flag to simulate it on your lights. Only one is active at a time.")
        note.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        note.setWordWrap(True)
        v.addWidget(note)

        grid_w = QWidget()
        grid   = QHBoxLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._flag_group = QButtonGroup(self)
        self._flag_group.setExclusive(True)

        for i, (label, key, color) in enumerate(_FLAGS):
            cell_w = QWidget()
            cell_h = QHBoxLayout(cell_w)
            cell_h.setContentsMargins(0, 0, 0, 0)
            cell_h.setSpacing(4)
            if key is not None:
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {color}; font-size: 13px;")
                cell_h.addWidget(dot)
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key is None)
            btn.setMinimumWidth(85)
            self._flag_group.addButton(btn, i)
            cell_h.addWidget(btn)
            grid.addWidget(cell_w)

        self._flag_group.idClicked.connect(self._on_flag_selected)
        v.addWidget(grid_w)
        v.addStretch()
        return w

    # ── Pit Limiter panel ─────────────────────────────────────────────────────

    def _build_pit_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 8)
        v.setSpacing(10)

        note = QLabel("Simulate the pit lane speed limiter being active.")
        note.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        v.addWidget(note)

        self._pit_btn = QPushButton("Activate Pit Limiter")
        self._pit_btn.setCheckable(True)
        self._pit_btn.setFixedWidth(200)
        self._pit_btn.setStyleSheet(
            "QPushButton:checked { background-color: #c07800; color: #000; border-color: #c07800; }"
        )
        self._pit_btn.toggled.connect(self._on_pit_toggled)
        v.addWidget(self._pit_btn)
        v.addStretch()
        return w

    # ── shared slider builder ─────────────────────────────────────────────────

    def _make_slider(self, layout, label: str, from_: int, to: int) -> tuple:
        row_w = QWidget()
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(6)

        lbl = QLabel(label)
        lbl.setFixedWidth(70)
        row_h.addWidget(lbl)

        sl = _NoScrollSlider(Qt.Orientation.Horizontal)
        sl.setRange(from_, to)
        sl.setValue(from_)
        sl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row_h.addWidget(sl, stretch=1)

        val_lbl = QLabel(str(from_))
        val_lbl.setFixedWidth(50)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet(f"color: {_MUTED};")
        row_h.addWidget(val_lbl)

        layout.addWidget(row_w)
        return sl, val_lbl

    # ── telemetry helpers ─────────────────────────────────────────────────────

    def _set_tel(self, key: str, value: float) -> None:
        with self._lock:
            self._telemetry[key] = value

    def _set_tel_ui(self, key: str, value: float) -> None:
        """Set telemetry and sync the matching slider widget."""
        with self._lock:
            self._telemetry[key] = value
        if key == "brake":
            self._brake_slider.blockSignals(True)
            self._brake_slider.setValue(int(value))
            self._brake_slider.blockSignals(False)
            self._brake_lbl.setText(str(int(value)))
        elif key == "rpm":
            self._rpm_slider.blockSignals(True)
            self._rpm_slider.setValue(int(value))
            self._rpm_slider.blockSignals(False)
            self._rpm_lbl.setText(str(int(value)))

    def _snap_rpm(self, fraction: float) -> None:
        self._ramp_rpm = False
        self._ramp_btn.setText("▶  Ramp RPM")
        with self._lock:
            max_rpm = self._telemetry["max_rpm"]
        self._set_tel_ui("rpm", max_rpm * fraction)

    # ── flag / pit controls ───────────────────────────────────────────────────

    def _on_flag_selected(self, idx: int) -> None:
        _label, key, _color = _FLAGS[idx]
        with self._lock:
            for _, fkey, _ in _FLAGS:
                if fkey:
                    self._telemetry[fkey] = 1.0 if fkey == key else 0.0

    def _on_pit_toggled(self, on: bool) -> None:
        self._pit_btn.setText("Deactivate Pit Limiter" if on else "Activate Pit Limiter")
        self._set_tel("pit_limiter", 1.0 if on else 0.0)

    # ── RPM / brake ramps ─────────────────────────────────────────────────────

    def _set_max_rpm(self) -> None:
        try:
            max_rpm = float(self._max_rpm_entry.text())
        except ValueError:
            return
        with self._lock:
            self._telemetry["max_rpm"] = max_rpm
        self._rpm_slider.setRange(0, int(max_rpm))

    def _toggle_ramp(self) -> None:
        if self._ramp_rpm:
            self._ramp_rpm = False
            self._ramp_btn.setText("▶  Ramp RPM")
        else:
            self._ramp_rpm = True
            self._ramp_btn.setText("■  Stop Ramp")
            threading.Thread(target=self._ramp_loop, daemon=True).start()

    def _ramp_loop(self) -> None:
        direction = 1
        current   = 0.0
        while self._ramp_rpm:
            with self._lock:
                max_rpm = self._telemetry["max_rpm"]
            step    = max_rpm / (RAMP_SECONDS * POLL_HZ)
            current = max(0.0, min(max_rpm, current + step * direction))
            direction = -1 if current >= max_rpm else (1 if current <= 0 else direction)
            with self._lock:
                self._telemetry["rpm"] = current
            time.sleep(POLL_INTERVAL)

    def _toggle_ramp_brake(self) -> None:
        if self._ramp_brake:
            self._ramp_brake = False
            self._ramp_brake_btn.setText("▶  Ramp Brake")
        else:
            self._ramp_brake = True
            self._ramp_brake_btn.setText("■  Stop Brake")
            threading.Thread(target=self._ramp_brake_loop, daemon=True).start()

    def _ramp_brake_loop(self) -> None:
        direction = 1
        current   = 0.0
        while self._ramp_brake:
            step    = 100.0 / (RAMP_SECONDS * POLL_HZ)
            current = max(0.0, min(100.0, current + step * direction))
            direction = -1 if current >= 100.0 else (1 if current <= 0.0 else direction)
            with self._lock:
                self._telemetry["brake"] = current
            time.sleep(POLL_INTERVAL)

    # ── effect management ─────────────────────────────────────────────────────

    def _selected_rev_mode(self) -> tuple[str, str]:
        for i, btn in enumerate(self._rev_mode_btns):
            if btn.isChecked():
                _, effect, mode = _REV_MODES[i]
                return effect, mode
        return "rev_counter", "center"

    def _get_active_effect_kwargs(self) -> dict:
        if self._get_effect_kwargs:
            kwargs = dict(self._get_effect_kwargs())
            rev_effect, counter_mode = self._selected_rev_mode()
            # Always run all effects in test mode regardless of main-tab checkboxes
            kwargs["active_effects"] = [
                rev_effect, "brake_lights", "flag_effect", "pit_limiter"
            ]
            kwargs["counter_mode"] = counter_mode
            if self._full_range_cb.isChecked():
                kwargs["start_rpm"]       = 0
                kwargs["start_threshold"] = 0.0
            return kwargs
        # Standalone: build from settings + config, always all effects
        s          = settings_manager.load()
        full_range = self._full_range_cb.isChecked()
        rev_effect, counter_mode = self._selected_rev_mode()
        active = [rev_effect, "brake_lights", "flag_effect", "pit_limiter"]
        return {
            "active_effects":             active,
            "start_rpm":                  0 if full_range else s.get("start_rpm", config.REV_START_RPM),
            "start_threshold":            0.0 if full_range else config.REV_START_THRESHOLD,
            "redline_threshold":          s.get("redline_pct", int(config.REV_REDLINE_THRESHOLD * 100)) / 100.0,
            "flash_interval":             config.REV_FLASH_INTERVAL,
            "transition_ms":              config.LIFX_TRANSITION_MS,
            "led_step":                   s.get("led_step",       config.LED_STEP),
            "counter_mode":               counter_mode,
            "strip_reversed":             s.get("strip_reversed", config.STRIP_REVERSED),
            "strip_max_brightness":       s.get("strip_brightness_pct", int(config.STRIP_MAX_BRIGHTNESS * 100)) / 100.0,
            "color_scheme":               s.get("color_scheme", "classic"),
            "brake_lights":               config.BRAKE_LIGHTS,
            "brake_threshold":            s.get("brake_threshold_pct",  int(config.BRAKE_THRESHOLD  * 100)) / 100.0,
            "brake_max_brightness":       s.get("brake_brightness_pct", int(config.BRAKE_MAX_BRIGHTNESS * 100)) / 100.0,
            "flag_lights":                config.FLAG_LIGHTS,
            "flag_max_brightness":        s.get("flag_brightness_pct",  int(config.FLAG_MAX_BRIGHTNESS * 100)) / 100.0,
            "pit_limiter_lights":         config.PIT_LIMITER_LIGHTS,
            "pit_limiter_brightness":     s.get("pit_limiter_brightness_pct", int(config.PIT_LIMITER_BRIGHTNESS * 100)) / 100.0,
            "pit_limiter_flash_interval": config.PIT_LIMITER_FLASH_INTERVAL,
        }

    def _rebuild_effects(self) -> None:
        if not self._rig:
            return
        kwargs = self._get_active_effect_kwargs()
        active = kwargs.get("active_effects", [])
        self._effects = [
            cls(self._rig, **kwargs)
            for name in active
            if (cls := EFFECTS.get(name))
        ]

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self, shared_rig: LightRig | None = None) -> None:
        """Connect to LIFX lights. Call from a background thread."""
        effect_kwargs = self._get_active_effect_kwargs()
        active        = effect_kwargs.get("active_effects", [])

        needed: set[str] = set()
        for name in active:
            cls = EFFECTS.get(name)
            if cls and hasattr(cls, "needed_lights"):
                needed.update(cls.needed_lights(**effect_kwargs))

        if shared_rig is not None:
            rig = shared_rig
            self._shared_rig = True
            for name in needed:
                ctrl = rig.get(name)
                if ctrl is not None and ctrl.connected:
                    continue
                cfg_entry = config.LIFX_LIGHTS.get(name, {})
                if not cfg_entry:
                    continue
                if ctrl is None:
                    ctrl = LIFXController(
                        ip=cfg_entry.get("ip"),
                        label=cfg_entry.get("label"),
                        discovery_timeout=cfg_entry.get("discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT),
                    )
                    rig.register(name, ctrl)
                ctrl.connect()
        else:
            rig = LightRig()
            self._shared_rig = False
            for name, cfg_entry in config.LIFX_LIGHTS.items():
                if name not in needed:
                    continue
                rig.register(name, LIFXController(
                    ip=cfg_entry.get("ip"),
                    label=cfg_entry.get("label"),
                    discovery_timeout=cfg_entry.get("discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT),
                ))
            rig.connect_all()

        connected = sum(1 for n in needed if (c := rig.get(n)) and c.connected)
        total     = len(needed)

        self._rig       = rig
        self._connected = connected > 0
        self._settings  = settings_manager.load()
        self._rebuild_effects()

        color = _GREEN if self._connected else _RED
        text  = f"{connected}/{total} lights connected" if total else "No lights needed"

        def _update_ui() -> None:
            self._status_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
            self._status_lbl.setText(text)
            if self._connected:
                self._poll_timer.start()

        if self._ui:
            self._ui.call.emit(_update_ui)
        else:
            QTimer.singleShot(0, _update_ui)

    def disconnect(self) -> None:
        self._ramp_rpm   = False
        self._ramp_brake = False
        self._connected  = False
        self._poll_timer.stop()
        if self._rig and not self._shared_rig:
            strip = self._rig.get("strip")
            if strip:
                strip.set_idle()

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self._connected:
            return

        with self._lock:
            tel = dict(self._telemetry)

        # Sync slider display (blockSignals prevents feedback loop)
        rpm   = int(tel["rpm"])
        brake = int(tel["brake"])
        self._rpm_slider.blockSignals(True)
        self._rpm_slider.setValue(rpm)
        self._rpm_slider.blockSignals(False)
        self._rpm_lbl.setText(str(rpm))
        self._brake_slider.blockSignals(True)
        self._brake_slider.setValue(brake)
        self._brake_slider.blockSignals(False)
        self._brake_lbl.setText(str(brake))

        for effect in self._effects:
            try:
                effect.update(tel)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Standalone window
# ─────────────────────────────────────────────────────────────────────────────

class TestHarness(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SimDeck — Test Harness")
        self.resize(560, 400)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)

        self._panel = TestPanel(central)
        v.addWidget(self._panel, stretch=1)

        QTimer.singleShot(100, lambda: threading.Thread(
            target=self._panel.connect, daemon=True).start())

    def closeEvent(self, event) -> None:
        self._panel.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _apply_dark_theme(app)
    window = TestHarness()
    window.show()
    sys.exit(app.exec())
