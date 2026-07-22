"""SimDeck main window and application entry point."""
from __future__ import annotations

import os
import sys
import threading
import urllib.request

import pystray
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabBar, QTabWidget, QVBoxLayout, QWidget, QLabel,
)

from simdeck import config, log_setup, settings_manager
from simdeck.engine import Engine
from simdeck.moza_pedals import MozaPedals
from simdeck.telemetry_logger import TelemetryLogger
from simdeck.udp_splitter import UDPSplitter

from .constants import _EXE_TO_NAME, _POLL_MS
from .helpers import (
    _apply_font_size, _check_for_update, _detect_game, _make_tray_image,
    _make_window_icon, _set_startup_registry,
)
from .overlay import TelemetryOverlay
from .theme import _apply_dark_theme, _build_stylesheet
from .widgets import _MainTabWidget, _UISignal
from .tabs.lifx import LIFXTab
from .tabs.lights import LightsTab
from .tabs.test import TestTab
from .tabs.splitter import SplitterTab
from .tabs.logger import LoggerTab
from .tabs.history import HistoryTab
from .tabs.settings import SettingsTab

log_setup.setup()


class SimDeckApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SimDeck")
        self.setWindowIcon(_make_window_icon())
        self.resize(860, 740)
        self.setMinimumSize(760, 620)

        self._ui   = _UISignal()
        settings   = settings_manager.load()
        self._tray = None

        _apply_font_size(settings.get("font_size_pt", 10))

        self._app_settings: dict = {
            "font_size_pt":         settings.get("font_size_pt",         10),
            "accent_color":         settings.get("accent_color",         "#f0a500"),
            "start_minimized":      settings.get("start_minimized",      False),
            "simhub_host":          settings.get("simhub_host",          config.SIMHUB_HOST),
            "simhub_port":          settings.get("simhub_port",          config.SIMHUB_PORT),
            "update_dot_pulse":     settings.get("update_dot_pulse",     True),
            "overlay_visible":      settings.get("overlay_visible",      False),
            "overlay_theme":        settings.get("overlay_theme",        "mirrored"),
            "overlay_bg_opacity":   settings.get("overlay_bg_opacity",   70),
            "overlay_line_opacity": settings.get("overlay_line_opacity", 100),
            "overlay_scale":        settings.get("overlay_scale",        100),
            "overlay_x":            settings.get("overlay_x",            None),
            "overlay_y":            settings.get("overlay_y",            None),
            "gradient_bg":          settings.get("gradient_bg",          True),
        }

        lights        = settings.get("lights", [])
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

        # ── Settings ───────────────────────────────────────────────────────
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
            on_overlay_preview_toggle=self._on_overlay_preview_toggle,
            on_gradient_change=self._on_gradient_change,
        )
        main_tabs.addTab(self._settings_tab, "Settings")

        def _on_main_tab_changed(idx: int) -> None:
            if main_tabs.tabText(idx) == "Lap Logs":
                self._history_tab.refresh()

        main_tabs.currentChanged.connect(_on_main_tab_changed)

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.setInterval(1500)
        self._restart_timer.timeout.connect(self._auto_restart)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        self._last_game_exe: str | None = None
        self._game_timer = QTimer(self)
        self._game_timer.setInterval(20000)
        self._game_timer.timeout.connect(self._check_game)
        self._game_timer.start()
        self._check_game()

        self._last_tray_status: str | None = None
        self._update_version: str | None   = None
        self._update_download_url: str     = ""
        self._setup_tray()

        initial_kwargs = self._lifx_tab.get_effect_kwargs()
        self._engine.start(initial_kwargs)
        self._splitter.start()

        self._moza = MozaPedals()
        self._moza.start()

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
        lights = self._lights_tab.get_lights()
        asgn   = self._lights_tab.get_assignments()
        lc     = {l["name"]: {"ip": l["ip"]} for l in lights}
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

    def _on_overlay_preview_toggle(self, enabled: bool) -> None:
        self._overlay.set_demo(enabled)
        if enabled:
            self._overlay.show()
        elif not self._app_settings.get("overlay_visible", False):
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
            self._update_version      = ver
            self._update_download_url = url
            self._ui.call.emit(lambda: self._tray.update_menu())
            self._ui.call.emit(lambda: self._settings_tab.set_update_available(ver, url))
            self._ui.call.emit(self._show_update_dot)

    def _manual_update_check(self) -> None:
        def _worker() -> None:
            result = _check_for_update()
            if result:
                ver, url = result
                self._update_version      = ver
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
        os.startfile(path)
        self._quit()

    def _setup_tray(self) -> None:
        from .constants import _RELEASES_PAGE
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

        self._poll_timer.stop()
        self._game_timer.stop()
        self._overlay._timer.stop()
        self._overlay.hide()

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


def main() -> None:
    app = QApplication(sys.argv)
    _init_settings = settings_manager.load()
    _init_accent   = _init_settings.get("accent_color", "#f0a500")
    _init_gradient = _init_settings.get("gradient_bg",  True)
    _apply_dark_theme(app, _init_accent, _init_gradient)
    window = SimDeckApp()
    if not window.start_minimized:
        window.show()
    sys.exit(app.exec())
