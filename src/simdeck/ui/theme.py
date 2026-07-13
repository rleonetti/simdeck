"""Dark theme stylesheet and palette helpers."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

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
