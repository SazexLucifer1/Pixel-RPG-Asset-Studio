"""Error dialog (View Log / Copy Error / Run Diagnostic) and log viewer."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.errors import ErrorReport
from pixel_rpg_studio.core.logging_setup import read_log_tail


def open_path(path: str | Path) -> None:
    """Open a file or folder with the system default application."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-requested
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class LogDialog(QDialog):
    def __init__(self, parent=None, title: str = "Application log", text: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 600)
        edit = QPlainTextEdit(self)
        edit.setReadOnly(True)
        edit.setFont(QFont("Consolas", 9))
        edit.setPlainText(text if text is not None else read_log_tail(1000))
        edit.moveCursor(edit.textCursor().MoveOperation.End)
        buttons = QHBoxLayout()
        folder = QPushButton("Open logs folder")
        folder.clicked.connect(lambda: open_path(paths.logs_dir()))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(edit.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (folder, copy):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(edit)
        layout.addLayout(buttons)


class ErrorDialog(QDialog):
    """Explains an error in plain language; technical details are one click away, never hidden."""

    def __init__(self, error: ErrorReport, parent=None, on_diagnostic=None) -> None:
        super().__init__(parent)
        self.error = error
        self.setWindowTitle("Something went wrong")
        self.resize(640, 360)
        title = QLabel(error.message)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        hint = QLabel(error.hint or "")
        hint.setWordWrap(True)
        hint.setVisible(bool(error.hint))
        self.details = QPlainTextEdit(error.details or "(no technical details)")
        self.details.setReadOnly(True)
        self.details.setFont(QFont("Consolas", 9))
        self.details.setVisible(False)
        toggle = QPushButton("Show details")
        toggle.clicked.connect(lambda: (self.details.setVisible(not self.details.isVisible()),
                                        toggle.setText("Hide details" if self.details.isVisible() else "Show details")))
        view_log = QPushButton("View Log")
        view_log.clicked.connect(lambda: LogDialog(self).exec())
        copy = QPushButton("Copy Error")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(error.full_text()))
        diag = QPushButton("Run Diagnostic")
        diag.setEnabled(on_diagnostic is not None)
        if on_diagnostic:
            diag.clicked.connect(lambda: (self.accept(), on_diagnostic()))
        row = QHBoxLayout()
        for b in (toggle, view_log, copy, diag):
            row.addWidget(b)
        row.addStretch(1)
        ok = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        ok.rejected.connect(self.reject)
        ok.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.details, 1)
        layout.addLayout(row)
        layout.addWidget(ok)


def confirm(parent, title: str, text: str, details: str = "") -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    if details:
        box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes
