"""Standalone helper functions for the SimDeck UI."""
from __future__ import annotations

import json
import sys
import urllib.request
import webbrowser

import psutil

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageDraw

from .constants import (
    _AUTO_GAME_TARGETS, _GREEN, _GREY, _KNOWN_GAMES, _RELEASES_PAGE,
    _RELEASES_URL, _YELLOW, __version__,
)

# ── Windows registry (startup) ────────────────────────────────────────────────
try:
    import winreg as _winreg
    _HAS_WINREG = True
except ImportError:
    _winreg = None
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


# ── App helpers ───────────────────────────────────────────────────────────────

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
        dot = (46, 204, 113, 255)
    elif simhub_status in ("waiting", "connecting"):
        dot = (240, 165, 0, 255)
    else:
        dot = (255, 255, 255, 40)
    draw.ellipse([18, 18, 46, 46], fill=dot)
    return img


def _make_window_icon() -> QIcon:
    size = 64
    pix  = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(0xC0, 0x78, 0x00))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(48)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "SD")
    p.end()
    return QIcon(pix)
