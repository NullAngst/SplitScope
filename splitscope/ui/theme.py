from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

BASE = "#15181f"
PANEL = "#1c2029"
RAISED = "#252a35"
LINE = "#323846"
TEXT = "#d5dae4"
MUTED = "#8a93a6"
ACCENT = "#7aa7ff"
WARN = "#ffb35c"
BAD = "#ff6b7a"

CHANNEL_COLORS = {
    "stereo": "#f2f4f8",
    "mid": "#6fd6ff",
    "side": "#ff8fd0",
    "left": "#ffc46b",
    "right": "#8ee08e",
}

# Prism stops: hue follows frequency (warm lows -> cool highs)
PRISM = [
    (0.00, "#ff5a5f"),
    (0.18, "#ff9f43"),
    (0.36, "#ffd84d"),
    (0.52, "#7be38a"),
    (0.68, "#45d4e6"),
    (0.84, "#6a8dff"),
    (1.00, "#b57bff"),
]

STYLE = f"""
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BASE}; }}
QFrame#panel, QWidget#panel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; }}
QLabel#heading {{ font-size: 11pt; font-weight: 600; color: {TEXT}; padding: 2px 0; }}
QLabel#hint {{ color: {MUTED}; font-size: 9pt; }}
QLabel#time {{ font-size: 15pt; font-weight: 600; font-family: "DejaVu Sans Mono", "Consolas", "Menlo", monospace; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {LINE}; spacing: 6px; padding: 4px; }}
QPushButton, QToolButton {{
    background: {RAISED}; border: 1px solid {LINE}; border-radius: 5px; padding: 5px 10px;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed, QToolButton:pressed {{ background: #303747; }}
QPushButton:checked, QToolButton:checked {{ background: #2f4470; border-color: {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #5c6475; }}
QPushButton#isolate {{ text-align: left; padding: 6px 8px; }}
QPushButton#isolate[dsp="true"], QPushButton#primary[dsp="true"] {{ border-style: dashed; color: {MUTED}; }}
QFrame#panel QToolButton {{ padding: 2px 4px; }}
QPushButton#primary {{ background: #2f4470; border-color: {ACCENT}; font-weight: 600; }}
QToolButton#mute:checked {{ background: #6b4a1f; border-color: {WARN}; }}
QToolButton#solo:checked {{ background: #1f5a3c; border-color: #6fe0a0; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 6px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: {BASE}; border: 1px solid {LINE}; padding: 5px 8px; border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {PANEL}; border-bottom-color: {PANEL}; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {BASE}; border: 1px solid {LINE}; border-radius: 4px; padding: 3px 6px;
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; background: {TEXT}; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::handle:vertical {{ height: 12px; margin: 0 -5px; background: {TEXT}; border-radius: 6px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 2px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QListWidget, QTableWidget {{ background: {BASE}; border: 1px solid {LINE}; border-radius: 4px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: #2f4470; }}
QHeaderView::section {{ background: {RAISED}; border: none; border-right: 1px solid {LINE}; padding: 4px; }}
QProgressBar {{ background: {BASE}; border: 1px solid {LINE}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; }}
QMenu::item:selected {{ background: #2f4470; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; }}
QSplitter::handle {{ background: {BASE}; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 6px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BASE))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(BASE))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(RAISED))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor("#2f4470"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(MUTED))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5c6475"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5c6475"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
