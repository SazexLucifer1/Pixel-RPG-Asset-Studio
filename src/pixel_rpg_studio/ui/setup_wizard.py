"""First-run setup wizard: explains exactly what is installed and what is missing."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from pixel_rpg_studio import APP_NAME
from pixel_rpg_studio.blender.detect import find_blender
from pixel_rpg_studio.comfyui.launcher import apply_install_to_settings, detect_install, find_installations
from pixel_rpg_studio.system.diagnostics import run_full_diagnostic, summary_table
from pixel_rpg_studio.ui.theme import STATUS_COLORS
from pixel_rpg_studio.ui.widgets.common import button
from pixel_rpg_studio.ui.widgets.dialogs import open_url


class _Bridge(QObject):
    done = Signal(object)
    progress = Signal(str)


class SetupWizard(QWizard):
    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self.setWindowTitle(f"{APP_NAME} – Setup")
        self.resize(820, 600)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(self._welcome())
        self.addPage(self._tools())
        self.addPage(self._check())
        self.button(QWizard.WizardButton.FinishButton).clicked.connect(self._finish)

    # ------------------------------------------------------------- pages
    def _welcome(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle(f"Welcome to {APP_NAME}")
        lay = QVBoxLayout(p)
        text = QLabel(
            "This tool produces consistent pixel-art assets for Godot using <b>local AI only</b>:<br><br>"
            "• <b>ComfyUI</b> (free) runs the image and 3D AI models on your NVIDIA GPU<br>"
            "• <b>Blender</b> (free) cleans up 3D models, rigs, animates and renders them<br>"
            "• Built-in deterministic tools turn everything into clean pixel art and sprite sheets<br><br>"
            "No paid services or API keys are needed. Large AI models are never downloaded without your explicit action.<br><br>"
            "The next pages detect what is installed and explain what is missing."
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(text)
        lay.addStretch(1)
        return p

    def _tools(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle("ComfyUI and Blender")
        p.setSubTitle("Select the installation folders. Leave empty to auto-detect.")
        lay = QVBoxLayout(p)
        s = self.services.settings
        self.comfy = QLineEdit(s.comfyui.install_dir)
        self.comfy_info = QLabel("")
        self.comfy_info.setWordWrap(True)
        self.comfy_info.setObjectName("Muted")
        row = QHBoxLayout()
        row.addWidget(self.comfy)
        row.addWidget(button("Choose Folder", self._pick_comfy))
        row.addWidget(button("Auto-detect", self._detect_comfy))
        lay.addWidget(QLabel("<b>ComfyUI</b> – Portable folder (with run_nvidia_gpu.bat), Desktop app (ComfyUI.exe) or a git install"))
        lay.addLayout(row)
        lay.addWidget(self.comfy_info)
        dl = QHBoxLayout()
        dl.addWidget(button("Open Download Page (Portable)", lambda: open_url("https://github.com/comfyanonymous/ComfyUI/releases")))
        dl.addWidget(button("Open Download Page (Desktop)", lambda: open_url("https://www.comfy.org/download")))
        dl.addStretch(1)
        lay.addLayout(dl)
        self.auto_start = QCheckBox("Start ComfyUI automatically with this application")
        self.auto_start.setChecked(s.comfyui.auto_start)
        lay.addWidget(self.auto_start)
        lay.addSpacing(16)
        self.blender = QLineEdit(s.blender.executable)
        self.blender.setPlaceholderText("auto-detect")
        self.blender_info = QLabel("")
        self.blender_info.setObjectName("Muted")
        brow = QHBoxLayout()
        brow.addWidget(self.blender)
        brow.addWidget(button("Choose blender.exe", self._pick_blender))
        brow.addWidget(button("Auto-detect", self._detect_blender))
        lay.addWidget(QLabel("<b>Blender</b> 3.6 LTS or newer"))
        lay.addLayout(brow)
        lay.addWidget(self.blender_info)
        lay.addWidget(button("Open Download Page (Blender)", lambda: open_url("https://www.blender.org/download/")), 0, Qt.AlignmentFlag.AlignLeft)
        self.mock = QCheckBox("I just want to try the interface first (demo mode with mock providers – no AI)")
        self.mock.setChecked(s.use_mock_providers)
        lay.addSpacing(16)
        lay.addWidget(self.mock)
        lay.addStretch(1)
        if not s.comfyui.install_dir:
            self._detect_comfy()
        else:
            self._describe_comfy()
        return p

    def _check(self) -> QWizardPage:
        p = QWizardPage()
        p.setTitle("System check")
        p.setSubTitle("Status of every component. Details and fixes are on the Diagnostics page.")
        lay = QVBoxLayout(p)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Component", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.check_status = QLabel("")
        lay.addWidget(self.table, 1)
        lay.addWidget(self.check_status)
        lay.addWidget(button("Run Diagnostic Again", self._run_check), 0, Qt.AlignmentFlag.AlignLeft)
        note = QLabel("Missing models? Open the AI Models page after setup – it shows download links and licenses. "
                      "You can finish setup now and come back later.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        lay.addWidget(note)
        self.bridge = _Bridge()
        self.bridge.done.connect(self._fill)
        self.bridge.progress.connect(self.check_status.setText)
        p.initializePage = self._run_check  # type: ignore[method-assign]
        return p

    # ------------------------------------------------------------ helpers
    def _apply(self) -> None:
        s = self.services.settings
        path = self.comfy.text().strip()
        inst = detect_install(path) if path else None
        if inst:
            apply_install_to_settings(inst, s)
        else:
            s.comfyui.install_dir = path
        s.comfyui.auto_start = self.auto_start.isChecked()
        s.blender.executable = self.blender.text().strip()
        s.use_mock_providers = self.mock.isChecked()

    def _pick_comfy(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "ComfyUI folder")
        if d:
            self.comfy.setText(d)
            self._describe_comfy()

    def _detect_comfy(self) -> None:
        found = find_installations()
        if found:
            inst = found[0]
            self.comfy.setText(str(inst.executable if inst.kind == "desktop" and inst.executable else inst.root))
        self._describe_comfy(none_text="No ComfyUI installation found automatically. Install ComfyUI or choose its folder.")

    def _describe_comfy(self, none_text: str = "Not recognised as a ComfyUI installation.") -> None:
        inst = detect_install(self.comfy.text().strip()) if self.comfy.text().strip() else None
        self.comfy_info.setText(f"✔ {inst.describe()}" + (f" – {'; '.join(inst.notes)}" if inst and inst.notes else "") if inst else none_text)

    def _pick_blender(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "blender.exe", "", "Blender (blender.exe blender);;All files (*)")
        if f:
            self.blender.setText(f)
            self._detect_blender()

    def _detect_blender(self) -> None:
        info = find_blender(self.blender.text().strip())
        if info:
            self.blender.setText(str(info.executable))
            self.blender_info.setText(f"✔ Blender {info.version_str}" + ("" if info.supported else " (too old – 3.6+ required)"))
        else:
            self.blender_info.setText("Blender not found. Install it (free) or choose blender.exe.")

    def _run_check(self) -> None:
        self._apply()
        self.check_status.setText("Checking...")
        settings, client = self.services.settings, self.services.client

        def work():
            self.bridge.done.emit(run_full_diagnostic(settings, client, progress=self.bridge.progress.emit))

        threading.Thread(target=work, daemon=True).start()

    def _fill(self, report) -> None:
        rows = summary_table(report)
        self.table.setRowCount(len(rows))
        for r, (name, status, detail) in enumerate(rows):
            for c, v in enumerate((name, status, detail)):
                item = QTableWidgetItem(v)
                if c == 1:
                    item.setForeground(QColor(STATUS_COLORS.get(status, "#8a8aa3")))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.check_status.setText("Check finished.")
        self.report = report

    def _finish(self) -> None:
        self._apply()
        self.services.settings.setup_completed = True
        self.services.apply_settings()
