"""Shared reusable widget primitives."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSlider, QTabBar, QTabWidget


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
