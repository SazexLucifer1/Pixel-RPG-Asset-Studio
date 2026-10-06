"""Dark creative-tool theme."""

ACCENT = "#7c5cff"
OK_COLOR = "#3ecf8e"
WARN_COLOR = "#f5b942"
ERROR_COLOR = "#ff5c6c"
MUTED = "#8a8aa3"

STATUS_COLORS = {"OK": OK_COLOR, "Info": MUTED, "Warning": WARN_COLOR, "Missing": WARN_COLOR, "Error": ERROR_COLOR,
                 "ok": OK_COLOR, "info": MUTED, "warning": WARN_COLOR, "missing": WARN_COLOR, "error": ERROR_COLOR}

STYLESHEET = f"""
QWidget {{ background: #17171f; color: #e6e6f0; font-size: 10pt; }}
QMainWindow::separator {{ background: #26263a; width: 1px; }}
QFrame#Sidebar {{ background: #11111a; border-right: 1px solid #26263a; }}
QListWidget#Nav {{ background: transparent; border: none; outline: 0; }}
QListWidget#Nav::item {{ padding: 7px 14px; border-radius: 6px; margin: 1px 6px; }}
QListWidget#Nav::item:selected {{ background: {ACCENT}; color: white; }}
QListWidget#Nav::item:hover:!selected {{ background: #23233a; }}
QLabel#AppTitle {{ font-size: 13pt; font-weight: 600; padding: 14px 14px 4px 14px; background: transparent; }}
QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel#Section {{ font-weight: 600; color: #c9c9ff; margin-top: 6px; }}
QGroupBox {{ border: 1px solid #2a2a40; border-radius: 8px; margin-top: 14px; padding: 8px; background: #1b1b26; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #c9c9ff; background: transparent; }}
QPushButton {{ background: #2a2a40; border: 1px solid #36365a; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ background: #34344f; }}
QPushButton:pressed {{ background: #23233a; }}
QPushButton:disabled {{ color: #6a6a80; background: #202030; border-color: #2a2a3a; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: #8d70ff; }}
QPushButton[danger="true"] {{ background: #4a1f2a; border-color: #6a2a3a; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #101018; border: 1px solid #2f2f48; border-radius: 5px; padding: 4px 6px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: #101018; border: 1px solid #2f2f48; selection-background-color: {ACCENT}; }}
QListWidget, QTreeWidget, QTableWidget {{ background: #101018; border: 1px solid #2a2a40; border-radius: 6px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: #3a3070; }}
QHeaderView::section {{ background: #1f1f2e; border: none; padding: 5px; color: #b8b8d0; }}
QTabWidget::pane {{ border: 1px solid #2a2a40; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: #1f1f2e; padding: 6px 14px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: #2c2c48; color: white; }}
QProgressBar {{ background: #101018; border: 1px solid #2a2a40; border-radius: 5px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{ background: #11111a; border-top: 1px solid #26263a; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: #24243a; color: white; border: 1px solid {ACCENT}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; }}
QSplitter::handle {{ background: #26263a; }}
"""
