"""Frameless always-on-top telemetry overlay."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from simdeck.engine import Engine
    from simdeck.moza_pedals import MozaPedals

_OVL_W        = 320
_OVL_H        = 80
_OVL_FPS      = 60
_OVL_HISTORY  = 600   # ~10s at 60Hz

# Per-theme base footprint (before the user's scale % is applied) — the bar
# meters read better tall-and-narrow than the wide trace graph.
_BASE_SIZE = {
    "mirrored": (320, 80),
    "lines":    (320, 80),
    "bars":     (170, 150),
}


class TelemetryOverlay(QWidget):
    """Frameless always-on-top window with three rendering themes."""

    THEME_MIRRORED = "mirrored"
    THEME_LINES    = "lines"
    THEME_BARS     = "bars"

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
        self._demo       = False
        self._demo_t0    = 0.0
        self._apply_size()

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // _OVL_FPS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _apply_size(self) -> None:
        base_w, base_h = _BASE_SIZE.get(self._theme, (_OVL_W, _OVL_H))
        self.setFixedSize(max(80, int(base_w * self._scale / 100)),
                          max(30, int(base_h * self._scale / 100)))

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
        self._apply_size()
        self.update()

    def set_demo(self, enabled: bool) -> None:
        """Feed synthetic pedal input instead of live telemetry — lets the
        overlay be positioned/styled without SimHub or pedal hardware."""
        self._demo = enabled
        if enabled:
            self._demo_t0 = time.monotonic()

    def _demo_values(self) -> tuple[float, float, float]:
        """Synthetic brake-then-accelerate cycle with a periodic clutch blip."""
        t = time.monotonic() - self._demo_t0
        thr = max(0.0, math.sin(t * 0.8))
        brk = max(0.0, -math.sin(t * 0.8))
        clu = 1.0 if (t % 4.0) < 0.4 else 0.0
        return thr, brk, clu

    def _tick(self) -> None:
        if self._demo:
            thr, brk, clu = self._demo_values()
        elif self._moza and self._moza.connected:
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
        elif self._theme == self.THEME_BARS:
            self._paint_bars(painter, thr, brk, clu, pad_x, pad_y, gw, gh)
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
        cy     = pad_y + gh / 2.0
        half_h = gh / 2.0 - 1
        n      = _OVL_HISTORY

        painter.setPen(QPen(QColor(70, 70, 70, int(180 * self._bg_alpha)), 1.0))
        painter.drawLine(pad_x, int(cy), pad_x + gw, int(cy))

        t_pts = [(xp(i), cy - v * half_h) for i, v in enumerate(thr)]
        b_pts = [(xp(i), cy + v * half_h) for i, v in enumerate(brk)]
        c_pts = [(xp(i), cy - v * half_h) for i, v in enumerate(clu)]

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

        painter.strokePath(self._smooth(t_pts),
                           QPen(QColor(0x2e, 0xcc, 0x71, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(b_pts),
                           QPen(QColor(0xe7, 0x4c, 0x3c, int(255 * self._line_alpha)), line_w))
        painter.strokePath(self._smooth(c_pts),
                           QPen(QColor(0x3a, 0x9b, 0xdc, int(200 * self._line_alpha)), line_w))

    def _paint_lines(self, painter, thr, brk, clu, xp, pad_x, pad_y, gw, gh, line_w) -> None:
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

    def _paint_bars(self, painter, thr, brk, clu, pad_x, pad_y, gw, gh) -> None:
        """Vertical THROTTLE / BRAKE / CLUTCH meters, motorsport-HUD style."""
        from PySide6.QtCore import QRectF

        scale  = self._scale / 100.0
        cur_t  = thr[-1] if thr else 0.0
        cur_b  = brk[-1] if brk else 0.0
        cur_c  = clu[-1] if clu else 0.0

        label_h = max(11.0, 12.0 * scale)
        value_h = max(11.0, 12.0 * scale)
        axis_w  = max(20.0, 22.0 * scale)

        font_label = QFont()
        font_label.setPointSizeF(max(6.0, 7.5 * scale))
        font_label.setBold(True)
        font_value = QFont()
        font_value.setPointSizeF(max(6.0, 8.0 * scale))

        track_top    = pad_y + label_h
        track_bottom = pad_y + gh - value_h
        track_h      = max(1.0, track_bottom - track_top)

        # Axis ticks: 100 / 50 / 0
        painter.setFont(font_value)
        for frac, text in ((0.0, "100"), (0.5, "50"), (1.0, "0")):
            y = track_top + frac * track_h
            painter.setPen(QPen(QColor(70, 70, 70, int(140 * self._bg_alpha)), 1.0))
            painter.drawLine(pad_x + axis_w, int(y), pad_x + gw, int(y))
            painter.setPen(QPen(QColor(170, 170, 170, int(220 * self._line_alpha))))
            painter.drawText(QRectF(pad_x, y - 7, axis_w - 4, 14),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)

        # Left-to-right matches real pedal layout: clutch, brake, throttle.
        bars = (
            ("CLU", cur_c, QColor(0x3a, 0x9b, 0xdc)),
            ("BRK", cur_b, QColor(0xe7, 0x4c, 0x3c)),
            ("THR", cur_t, QColor(0x2e, 0xcc, 0x71)),
        )
        cols_x0 = pad_x + axis_w
        cols_w  = gw - axis_w
        gap     = max(4.0, 6.0 * scale)
        bar_w   = max(4.0, (cols_w - gap * (len(bars) - 1)) / len(bars))

        for i, (label, value, color) in enumerate(bars):
            x = cols_x0 + i * (bar_w + gap)

            painter.setFont(font_label)
            painter.setPen(QPen(QColor(220, 220, 220, int(230 * self._line_alpha))))
            painter.drawText(QRectF(x, pad_y, bar_w, label_h),
                             Qt.AlignmentFlag.AlignCenter, label)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(40, 40, 40, int(160 * self._bg_alpha))))
            painter.drawRoundedRect(QRectF(x, track_top, bar_w, track_h), 3, 3)

            fill_h = max(0.0, min(1.0, value)) * track_h
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(),
                                           int(230 * self._line_alpha))))
            painter.drawRoundedRect(QRectF(x, track_top + track_h - fill_h, bar_w, fill_h), 3, 3)

            painter.setFont(font_value)
            painter.setPen(QPen(QColor(230, 230, 230, int(230 * self._line_alpha))))
            painter.drawText(QRectF(x, track_bottom + 2, bar_w, value_h),
                             Qt.AlignmentFlag.AlignCenter, f"{int(round(value * 100))}")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
