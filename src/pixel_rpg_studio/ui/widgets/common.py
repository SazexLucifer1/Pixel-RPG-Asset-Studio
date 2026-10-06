"""Small shared widget helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pixel_rpg_studio.ui.theme import STATUS_COLORS


def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 6)
    t = QLabel(title)
    t.setObjectName("PageTitle")
    layout.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        layout.addWidget(s)
    return w


def button(text: str, slot=None, primary: bool = False, tooltip: str = "", danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if tooltip:
        b.setToolTip(tooltip)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def status_label(status: str, text: str | None = None) -> QLabel:
    lbl = QLabel(text or status)
    color = STATUS_COLORS.get(status, "#8a8aa3")
    lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
    return lbl


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #2a2a40;")
    return f


def row(*widgets, stretch: bool = True) -> QHBoxLayout:
    layout = QHBoxLayout()
    for w in widgets:
        layout.addWidget(w)
    if stretch:
        layout.addStretch(1)
    return layout
