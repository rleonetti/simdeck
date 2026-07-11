"""
SimDeck Test Harness — interactive telemetry simulator.

TestPanel is an embeddable QWidget; TestHarness wraps it as a standalone window.
Call TestPanel.connect() in a background thread after building.

Usage: python test_harness.py
"""

import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QCheckBox, QPushButton, QLineEdit,
    QFrame,
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

_ANIM_STYLES: dict[str, tuple[str, str]] = {
    "Center":     ("rev_counter", "center"),
    "Left/Right": ("rev_counter", "left_right"),
    "Full":       ("rev_counter", "full"),
    "Rev Lights": ("rev_lights",  "left_right"),
}


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
    """Embeddable test controls. Call connect() in a background thread after adding to a layout."""

    def __init__(self, parent: QWidget, ui: "_UISignal | None" = None) -> None:
        super().__init__(parent)

        self._ui                = ui
        self._effects: list     = []
        self._rig               = None
        self._connected         = False
        self._shared_rig        = False
        self._ramp_active       = False
        self._ramp_brake_active = False
        self._lock              = threading.Lock()
        self._telemetry         = {"rpm": 0.0, "max_rpm": 8500.0, "brake": 0.0}
        self._settings: dict    = {}

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(int(POLL_INTERVAL * 1000))
        self._poll_timer.timeout.connect(self._poll)

        self._build()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Status row
        top_w = QWidget()
        top_h = QHBoxLayout(top_w)
        top_h.setContentsMargins(12, 8, 12, 4)
        top_h.addStretch()
        self._status_lbl = QLabel("Connecting…")
        self._status_lbl.setStyleSheet(f"color: {_MUTED};")
        top_h.addWidget(self._status_lbl)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {_YELLOW}; font-size: 18px;")
        top_h.addWidget(self._status_dot)
        v.addWidget(top_w)

        # Max RPM + Full range row
        cfg_w = QWidget()
        cfg_h = QHBoxLayout(cfg_w)
        cfg_h.setContentsMargins(12, 0, 12, 0)
        cfg_h.setSpacing(6)
        cfg_h.addWidget(QLabel("Max RPM"))
        self._max_rpm_entry = QLineEdit("8500")
        self._max_rpm_entry.setFixedWidth(80)
        cfg_h.addWidget(self._max_rpm_entry)
        set_btn = QPushButton("Set")
        set_btn.setFixedSize(50, 28)
        set_btn.clicked.connect(self._set_max_rpm)
        cfg_h.addWidget(set_btn)
        cfg_h.addStretch()
        self._full_range_cb = QCheckBox("Full range")
        self._full_range_cb.setChecked(True)
        self._full_range_cb.stateChanged.connect(lambda _: self._rebuild_effects())
        cfg_h.addWidget(self._full_range_cb)
        v.addWidget(cfg_w)

        # Animation style selector
        anim_w = QWidget()
        anim_h = QHBoxLayout(anim_w)
        anim_h.setContentsMargins(12, 0, 12, 0)
        anim_h.setSpacing(6)
        anim_lbl = QLabel("Animation")
        anim_lbl.setFixedWidth(80)
        anim_h.addWidget(anim_lbl)
        self._anim_btns: dict[str, QPushButton] = {}
        from PySide6.QtWidgets import QButtonGroup
        self._anim_group = QButtonGroup(self)
        self._anim_group.setExclusive(True)
        for i, name in enumerate(_ANIM_STYLES):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(28)
            self._anim_group.addButton(btn, i)
            anim_h.addWidget(btn)
            self._anim_btns[name] = btn
        self._anim_group.idClicked.connect(lambda _: self._rebuild_effects())
        anim_h.addStretch()
        v.addWidget(anim_w)

        # Sliders panel
        panel_f = QFrame()
        panel_f.setFrameShape(QFrame.Shape.StyledPanel)
        panel_v = QVBoxLayout(panel_f)
        panel_v.setContentsMargins(12, 8, 12, 8)
        panel_v.setSpacing(5)
        self._rpm_slider,   self._rpm_lbl   = self._make_slider(panel_v, "RPM",     0, 8500)
        self._brake_slider, self._brake_lbl = self._make_slider(panel_v, "Brake %", 0,  100)
        v.addWidget(panel_f)

        # Action buttons
        btns_w = QWidget()
        btns_h = QHBoxLayout(btns_w)
        btns_h.setContentsMargins(12, 0, 12, 0)
        btns_h.setSpacing(8)
        self._ramp_btn = QPushButton("▶  Ramp RPM")
        self._ramp_btn.setFixedWidth(130)
        self._ramp_btn.clicked.connect(self._toggle_ramp)
        btns_h.addWidget(self._ramp_btn)
        self._ramp_brake_btn = QPushButton("▶  Ramp Brake")
        self._ramp_brake_btn.setFixedWidth(130)
        self._ramp_brake_btn.clicked.connect(self._toggle_ramp_brake)
        btns_h.addWidget(self._ramp_brake_btn)
        reset_btn = QPushButton("Reset")
        reset_btn.setFixedWidth(80)
        reset_btn.setStyleSheet("QPushButton { background: transparent; border: 1px solid #555; }")
        reset_btn.clicked.connect(self._reset)
        btns_h.addWidget(reset_btn)
        btns_h.addStretch()
        v.addWidget(btns_w)

        # Scenario shortcuts
        sc_hdr = QLabel("SCENARIOS")
        sc_hdr.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {_MUTED}; padding-left: 12px; padding-top: 6px;")
        v.addWidget(sc_hdr)
        sc_w = QWidget()
        sc_h = QHBoxLayout(sc_w)
        sc_h.setContentsMargins(12, 0, 12, 0)
        sc_h.setSpacing(6)
        for label, fn in (
            ("Idle",    self._scenario_idle),
            ("Mid Rev", self._scenario_mid),
            ("Redline", self._scenario_redline),
            ("Braking", self._scenario_braking),
        ):
            btn = QPushButton(label)
            btn.setFixedWidth(90)
            btn.clicked.connect(fn)
            sc_h.addWidget(btn)
        sc_h.addStretch()
        v.addWidget(sc_w)
        v.addStretch()

    def _make_slider(self, layout: QVBoxLayout, label: str, from_: int, to: int) -> tuple:
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

    # ── controls ──────────────────────────────────────────────────────────────

    def _on_rpm(self, v: int) -> None:
        with self._lock:
            self._telemetry["rpm"] = float(v)

    def _on_brake(self, v: int) -> None:
        with self._lock:
            self._telemetry["brake"] = float(v)

    def _set_max_rpm(self) -> None:
        try:
            max_rpm = float(self._max_rpm_entry.text())
        except ValueError:
            return
        with self._lock:
            self._telemetry["max_rpm"] = max_rpm
        self._rpm_slider.setRange(0, int(max_rpm))

    def _toggle_ramp(self) -> None:
        if self._ramp_active:
            self._ramp_active = False
            self._ramp_btn.setText("▶  Ramp RPM")
        else:
            self._ramp_active = True
            self._ramp_btn.setText("■  Stop Ramp")
            threading.Thread(target=self._ramp_loop, daemon=True).start()

    def _ramp_loop(self) -> None:
        direction = 1
        current   = 0.0
        while self._ramp_active:
            with self._lock:
                max_rpm = self._telemetry["max_rpm"]
            step    = max_rpm / (RAMP_SECONDS * POLL_HZ)
            current = max(0.0, min(max_rpm, current + step * direction))
            if current >= max_rpm:
                direction = -1
            elif current <= 0:
                direction = 1
            with self._lock:
                self._telemetry["rpm"] = current
            time.sleep(POLL_INTERVAL)

    def _toggle_ramp_brake(self) -> None:
        if self._ramp_brake_active:
            self._ramp_brake_active = False
            self._ramp_brake_btn.setText("▶  Ramp Brake")
        else:
            self._ramp_brake_active = True
            self._ramp_brake_btn.setText("■  Stop Brake")
            threading.Thread(target=self._ramp_brake_loop, daemon=True).start()

    def _ramp_brake_loop(self) -> None:
        direction = 1
        current   = 0.0
        while self._ramp_brake_active:
            step    = 100.0 / (RAMP_SECONDS * POLL_HZ)
            current = max(0.0, min(100.0, current + step * direction))
            if current >= 100.0:
                direction = -1
            elif current <= 0.0:
                direction = 1
            with self._lock:
                self._telemetry["brake"] = current
            time.sleep(POLL_INTERVAL)

    def _reset(self) -> None:
        self._ramp_active       = False
        self._ramp_brake_active = False
        self._ramp_btn.setText("▶  Ramp RPM")
        self._ramp_brake_btn.setText("▶  Ramp Brake")
        with self._lock:
            self._telemetry["rpm"]   = 0.0
            self._telemetry["brake"] = 0.0

    # ── scenarios ─────────────────────────────────────────────────────────────

    def _scenario_idle(self) -> None:
        self._ramp_active = False
        self._ramp_btn.setText("▶  Ramp RPM")
        with self._lock:
            self._telemetry["rpm"]   = 0.0
            self._telemetry["brake"] = 0.0

    def _scenario_mid(self) -> None:
        self._ramp_active = False
        self._ramp_btn.setText("▶  Ramp RPM")
        with self._lock:
            self._telemetry["rpm"]   = self._telemetry["max_rpm"] * 0.65
            self._telemetry["brake"] = 0.0

    def _scenario_redline(self) -> None:
        self._ramp_active = False
        self._ramp_btn.setText("▶  Ramp RPM")
        with self._lock:
            self._telemetry["rpm"]   = self._telemetry["max_rpm"] * 0.98
            self._telemetry["brake"] = 0.0

    def _scenario_braking(self) -> None:
        self._ramp_active = False
        self._ramp_btn.setText("▶  Ramp RPM")
        with self._lock:
            self._telemetry["rpm"]   = 0.0
            self._telemetry["brake"] = 80.0

    # ── connection ────────────────────────────────────────────────────────────

    def _selected_anim(self) -> str:
        for name, btn in self._anim_btns.items():
            if btn.isChecked():
                return name
        return "Center"

    def _build_effect_kwargs(self, s: dict) -> dict:
        full_range = self._full_range_cb.isChecked()
        rev_effect, counter_mode = _ANIM_STYLES.get(self._selected_anim(), ("rev_counter", "center"))

        active = [rev_effect]
        if "brake_lights" in config.ACTIVE_EFFECTS:
            active.append("brake_lights")

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

    def connect(self, shared_rig: LightRig | None = None) -> None:
        """Connect to LIFX lights and start the poll loop. Call from a background thread."""
        s             = settings_manager.load()
        effect_kwargs = self._build_effect_kwargs(s)
        active        = effect_kwargs["active_effects"]

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

        connected = sum(1 for name in needed if (c := rig.get(name)) and c.connected)
        total     = len(needed)

        self._rig       = rig
        self._connected = connected > 0
        self._settings  = s

        self._rebuild_effects_from_kwargs(effect_kwargs)

        color = _GREEN if self._connected else _RED
        text  = f"{connected}/{total} lights connected" if total else "No lights needed"

        def _update_ui() -> None:
            self._status_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
            self._status_lbl.setText(text)
            if self._connected:
                self._poll_timer.start()

        if self._ui:
            self._ui.call.emit(_update_ui)
        else:
            QTimer.singleShot(0, _update_ui)

    def disconnect(self) -> None:
        """Stop polling and release lights."""
        self._ramp_active       = False
        self._ramp_brake_active = False
        self._connected         = False
        self._poll_timer.stop()
        if self._rig:
            strip = self._rig.get("strip")
            if strip:
                strip.set_idle()

    def _rebuild_effects(self) -> None:
        if not self._rig:
            return
        s = self._settings or settings_manager.load()
        self._rebuild_effects_from_kwargs(self._build_effect_kwargs(s))

    def _rebuild_effects_from_kwargs(self, effect_kwargs: dict) -> None:
        active  = effect_kwargs["active_effects"]
        effects = []
        for name in active:
            cls = EFFECTS.get(name)
            if cls:
                effects.append(cls(self._rig, **effect_kwargs))
        self._effects = effects

    # ── poll ──────────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self._connected:
            return

        with self._lock:
            tel = dict(self._telemetry)

        rpm   = int(tel["rpm"])
        brake = int(tel["brake"])
        self._rpm_slider.setValue(rpm)
        self._rpm_lbl.setText(str(rpm))
        self._brake_slider.setValue(brake)
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
        self.resize(480, 460)
        self.setFixedSize(480, 460)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 12, 0, 0)

        hdr_w = QWidget()
        hdr_h = QHBoxLayout(hdr_w)
        hdr_h.setContentsMargins(12, 0, 12, 0)
        title = QLabel("Test Harness")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        hdr_h.addWidget(title)
        v.addWidget(hdr_w)

        self._panel = TestPanel(central)
        v.addWidget(self._panel, stretch=1)

        QTimer.singleShot(100, lambda: threading.Thread(target=self._panel.connect, daemon=True).start())

    def closeEvent(self, event) -> None:
        self._panel.disconnect()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _apply_dark_theme(app)
    window = TestHarness()
    window.show()
    sys.exit(app.exec())
