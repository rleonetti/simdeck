"""LIFX Effects tab."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from simdeck import config
from simdeck.effects import EFFECTS
from simdeck.engine import Engine
from ..constants import (
    _ALL_EFFECTS, _AMBER, _FLAG_DESC, _FLAG_DISPLAY, _FLAG_DOT_COLOR, _FLAG_ORDER,
    _GREEN, _GREY, _MODE_LABELS, _MODE_VALUES, _MUTED, _SCHEME_LABELS, _SCHEME_VALUES,
    _SPINNER, _YELLOW,
)
from ..helpers import _dot_color
from ..widgets import _NoScrollSlider, _UISignal


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

        body_w = QWidget()
        body   = QHBoxLayout(body_w)
        body.setContentsMargins(8, 0, 8, 8)
        body.setSpacing(4)
        root.addWidget(body_w, stretch=1)

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
        self._scheme_combo.setFixedWidth(280)
        cs_h.addStretch()
        cs_h.insertWidget(2, self._scheme_combo)
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

        brake_hdr   = self._section(right, "BRAKE LIGHTS")
        brake_group = QWidget()
        brake_v     = QVBoxLayout(brake_group)
        brake_v.setContentsMargins(10, 0, 10, 4)
        brake_v.setSpacing(3)
        self._brake_thr = self._slider(brake_v, "Threshold %",  s["brake_threshold_pct"],  0,  20)
        self._brake_bri = self._slider(brake_v, "Brightness %", s["brake_brightness_pct"],  0, 100)
        right.addWidget(brake_group)
        self._effect_groups["brake_lights"] = (brake_hdr, brake_group)

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

        pit_hdr   = self._section(right, "PIT LIMITER")
        pit_group = QWidget()
        pit_v     = QVBoxLayout(pit_group)
        pit_v.setContentsMargins(10, 0, 10, 4)
        pit_v.setSpacing(3)
        self._pit_bri = self._slider(pit_v, "Brightness %", s.get("pit_limiter_brightness_pct", 75), 0, 100)
        right.addWidget(pit_group)
        self._effect_groups["pit_limiter"] = (pit_hdr, pit_group)

        right.addStretch()

        for name, cb in self._effect_checks.items():
            if name in self._effect_groups:
                self._set_group_state(name, cb.isChecked())

        self._rebuild_lights_panel()

    def _section(self, layout: QVBoxLayout, text: str) -> QLabel:
        self._section_count += 1
        if self._section_count > 1:
            from PySide6.QtWidgets import QFrame as _QFrame
            sep = _QFrame()
            sep.setFrameShape(_QFrame.Shape.HLine)
            sep.setFrameShadow(_QFrame.Shadow.Plain)
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

        sl.valueChanged.connect(lambda v, ve=val_edit: ve.setText(str(v)))

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

    def _set_group_state(self, effect_name: str, enabled: bool) -> None:
        if effect_name not in self._effect_groups:
            return
        hdr, group = self._effect_groups[effect_name]
        color = "#d5d5d5" if enabled else _MUTED
        hdr.setStyleSheet(
            f"font-size: 19px; font-weight: bold; color: {color};"
            " padding-top: 12px; padding-bottom: 2px; padding-left: 10px;"
        )
        group.setEnabled(True)
        group.setGraphicsEffect(None)
        if not enabled:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(group)
            eff.setOpacity(0.45)
            group.setGraphicsEffect(eff)

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
        lay = self._lights_container.layout()
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
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

    def _spin(self) -> None:
        self._spinner_lbl.setText(_SPINNER[self._spinner_idx % len(_SPINNER)])
        self._spinner_idx += 1

    def _start_spinner(self) -> None:
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        self._spinner_timer.stop()
        self._spinner_lbl.setText("")

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
