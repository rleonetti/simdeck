"""Light registry and effect-assignment tab."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QScrollArea, QVBoxLayout, QWidget,
    QComboBox,
)

from ..constants import _GREEN, _GREY, _MUTED
from ..helpers import _dot_color

_EFFECT_LABELS = [
    ("Rev Counter",  "rev_counter"),
    ("Brake Lights", "brake_lights"),
    ("Flag Effect",  "flag_effect"),
    ("Pit Limiter",  "pit_limiter"),
]


class _LightEditDialog(QDialog):
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
        self._known_ips      = known_ips or []
        self._discovered:  list[dict] = []
        self._added:       list[dict] = []

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

            subnets: set[str] = set()
            for ip in self._known_ips:
                parts = ip.split(".")
                if len(parts) == 4:
                    subnets.add(".".join(parts[:3]))

            if not subnets:
                for addr in orig:
                    parts = addr.split(".")
                    if len(parts) == 4 and parts[-1] == "255":
                        subnets.add(".".join(parts[:3]))

            found: dict[str, str] = {}
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

            result: list[dict] = []
            for mac, ip in sorted(found.items(), key=lambda x: tuple(int(p) for p in x[1].split("."))):
                try:
                    light = Light(mac, ip)
                    label = light.get_label() or ""
                    mz    = light.supports_multizone()
                    result.append({"ip": ip, "label": label, "type": "multizone" if mz else "single"})
                except Exception:
                    result.append({"ip": ip, "label": "", "type": "single"})

            self._scan_status_text = f"Found {len(result)} light(s)." if result else "No lights found."
        except Exception as exc:
            result = []
            self._scan_status_text = f"Scan failed: {exc}"
        self._scan_done.emit(result)

    def _finish_scan(self, result: list) -> None:
        registered       = set(self._known_ips)
        self._discovered = [d for d in result if d["ip"] not in registered]
        skipped          = len(result) - len(self._discovered)
        status           = getattr(self, "_scan_status_text", "")
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
        d   = self._discovered[idx]
        if not self._name_edit.text():
            self._name_edit.setText(d.get("label", "").lower().replace(" ", "_") or "light")
        self._add_btn.setEnabled(True)

    def _add_selected(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        idx  = self._list.row(items[0])
        d    = self._discovered[idx]
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


class LightsTab(QWidget):
    def __init__(self, settings: dict, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._on_change   = on_change
        self._lights:      list[dict] = list(settings.get("lights", []))
        self._assignments: dict       = {
            k: list(v) for k, v in settings.get("effect_lights", {
                "rev_counter": [], "brake_lights": [], "flag_effect": [], "pit_limiter": [],
            }).items()
        }
        self._status_dots:            dict[str, QLabel]              = {}
        self._assign_checks:          dict[str, dict[str, QCheckBox]] = {}
        self._assign_section_layouts: dict[str, QVBoxLayout]          = {}
        self._build()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

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

        self._registry_scroll   = QScrollArea()
        self._registry_scroll.setWidgetResizable(True)
        self._registry_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._registry_content  = QWidget()
        self._registry_layout   = QVBoxLayout(self._registry_content)
        self._registry_layout.setContentsMargins(0, 0, 0, 0)
        self._registry_layout.setSpacing(2)
        self._registry_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._registry_scroll.setWidget(self._registry_content)
        left_v.addWidget(self._registry_scroll, stretch=1)

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

        self._asgn_scroll   = QScrollArea()
        self._asgn_scroll.setWidgetResizable(True)
        self._asgn_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._asgn_content  = QWidget()
        self._asgn_layout   = QVBoxLayout(self._asgn_content)
        self._asgn_layout.setContentsMargins(0, 4, 0, 4)
        self._asgn_layout.setSpacing(0)
        self._asgn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._asgn_scroll.setWidget(self._asgn_content)
        right_v.addWidget(self._asgn_scroll, stretch=1)

        outer.addWidget(right_f, stretch=1)

        self._rebuild_registry()
        self._rebuild_assignments()

    def _rebuild_registry(self) -> None:
        self._selected_name: str | None   = None
        self._registry_rows: dict[str, QWidget] = {}
        self._status_dots = {}

        while self._registry_layout.count():
            item = self._registry_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for light in self._lights:
            self._add_registry_row(light)

    def _add_registry_row(self, light: dict) -> None:
        name  = light["name"]
        row   = QWidget()
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
        for n, row in self._registry_rows.items():
            row.setStyleSheet("background: #2a2a2a; border-radius: 4px;" if n == name else "")
        self._selected_name = name

    def _rebuild_assignments(self) -> None:
        self._assign_checks           = {}
        self._assign_section_layouts  = {}

        while self._asgn_layout.count():
            item = self._asgn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for label, key in _EFFECT_LABELS:
            hdr = QLabel(label.upper())
            hdr.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {_MUTED}; letter-spacing: 1px; margin-top: 10px;")
            self._asgn_layout.addWidget(hdr)

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
                    cb   = QCheckBox(name)
                    cb.setChecked(name in assigned)
                    cb.toggled.connect(self._on_assignment_changed)
                    section_v.addWidget(cb)
                    self._assign_checks[key][name] = cb

    def _refresh_assignment_section(self, key: str) -> None:
        section_v = self._assign_section_layouts.get(key)
        if section_v is None:
            return
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
                cb   = QCheckBox(name)
                cb.setChecked(name in assigned)
                cb.toggled.connect(self._on_assignment_changed)
                section_v.addWidget(cb)
                self._assign_checks[key][name] = cb

    def _on_assignment_changed(self) -> None:
        self._on_change()

    def _on_add(self, _=None) -> None:
        dlg = _LightEditDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        light = dlg.result_light()
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
        name  = self._selected_name
        if not name:
            return
        light = next((l for l in self._lights if l["name"] == name), None)
        if not light:
            return
        dlg = _LightEditDialog(self, light)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated  = dlg.result_light()
        old_name = light["name"]
        light.update(updated)
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

    def get_lights(self) -> list[dict]:
        return list(self._lights)

    def get_assignments(self) -> dict:
        result = {}
        for effect, checks in self._assign_checks.items():
            result[effect] = [name for name, cb in checks.items() if cb.isChecked()]
        for key in ("rev_counter", "brake_lights", "flag_effect", "pit_limiter"):
            if key not in result:
                result[key] = list(self._assignments.get(key, []))
        return result

    def get_settings(self) -> dict:
        return {
            "lights":        self.get_lights(),
            "effect_lights": self.get_assignments(),
        }

    def update_light_status(self, status: dict) -> None:
        for name, dot in self._status_dots.items():
            s = status.get(name, "idle")
            dot.setStyleSheet(f"color: {_dot_color(s)}; font-size: 16px;")
