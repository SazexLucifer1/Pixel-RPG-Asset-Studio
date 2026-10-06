"""Main window: sidebar navigation, page stack and status bar (AI status, GPU, queue, progress)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio import APP_NAME, __version__
from pixel_rpg_studio.core.errors import ErrorReport, StudioError, translate_exception
from pixel_rpg_studio.core.jobs import JobState
from pixel_rpg_studio.ui.backend import BackendManager
from pixel_rpg_studio.ui.pages.character_studio import CharacterStudioPage
from pixel_rpg_studio.ui.pages.object_studio import ObjectStudioPage
from pixel_rpg_studio.ui.pages.system_pages import DashboardPage, DiagnosticsPage, ModelsPage, ProjectsPage, SettingsPage, StylePage
from pixel_rpg_studio.ui.pages.twod_studios import BackgroundStudioPage, SpriteSheetPage, TileStudioPage, VFXStudioPage
from pixel_rpg_studio.ui.theme import ERROR_COLOR, MUTED, OK_COLOR, WARN_COLOR
from pixel_rpg_studio.ui.widgets.dialogs import ErrorDialog, LogDialog

log = logging.getLogger(__name__)

NAV = [
    "Dashboard", "Projects", "Characters", "Weapons", "Items", "Props", "Environment", "Buildings", "Tiles",
    "Backgrounds", "VFX", "Sprite Sheets", "Style", "AI Models", "Settings", "Diagnostics",
]


class MainWindow(QMainWindow):
    def __init__(self, services) -> None:
        super().__init__()
        self.services = services
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 900)
        services.error_handler = self.show_error
        services.job_updated.connect(self._job_updated)
        services.job_finished.connect(self._job_finished)
        services.message.connect(lambda m: self.statusBar().showMessage(m, 8000))
        services.project_changed.connect(self._project_changed)
        self.backend = BackendManager(services)

        # sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 8)
        title = QLabel("Pixel RPG\nAsset Studio")
        title.setObjectName("AppTitle")
        sl.addWidget(title)
        self.project_label = QLabel("No project")
        self.project_label.setObjectName("Muted")
        self.project_label.setContentsMargins(14, 0, 8, 8)
        self.project_label.setWordWrap(True)
        sl.addWidget(self.project_label)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.addItems(NAV)
        sl.addWidget(self.nav, 1)
        if services.providers.mock:
            demo = QLabel("DEMO MODE\nmock providers, no real AI")
            demo.setStyleSheet(f"color: {WARN_COLOR}; padding: 6px 14px; font-weight: 600;")
            sl.addWidget(demo)

        # pages
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        factories = {
            "Dashboard": lambda: DashboardPage(services, self),
            "Projects": lambda: ProjectsPage(services, self),
            "Characters": lambda: CharacterStudioPage(services, self),
            "Weapons": lambda: ObjectStudioPage(services, self, "weapon"),
            "Items": lambda: ObjectStudioPage(services, self, "item"),
            "Props": lambda: ObjectStudioPage(services, self, "prop"),
            "Environment": lambda: ObjectStudioPage(services, self, "environment"),
            "Buildings": lambda: ObjectStudioPage(services, self, "building"),
            "Tiles": lambda: TileStudioPage(services, self),
            "Backgrounds": lambda: BackgroundStudioPage(services, self),
            "VFX": lambda: VFXStudioPage(services, self),
            "Sprite Sheets": lambda: SpriteSheetPage(services, self),
            "Style": lambda: StylePage(services, self),
            "AI Models": lambda: ModelsPage(services, self),
            "Settings": lambda: SettingsPage(services, self),
            "Diagnostics": lambda: DiagnosticsPage(services, self),
        }
        for name in NAV:
            try:
                page = factories[name]()
            except Exception as exc:  # noqa: BLE001 - a broken page must not kill the app
                log.exception("Page %s failed to load", name)
                page = QLabel(f"This page failed to load:\n{translate_exception(exc).message}\nSee View Log for details.")
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
            container = QWidget()
            cl = QVBoxLayout(container)
            cl.setContentsMargins(18, 12, 18, 8)
            cl.addWidget(page)
            self.pages[name] = page
            self.stack.addWidget(container)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        cl = QHBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(sidebar)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self._build_status_bar()

        self.backend.status_changed.connect(self._backend_status)
        self.backend.starting.connect(lambda m: self._set_ai(m, WARN_COLOR))
        self.backend.started.connect(lambda: (self._set_ai("ComfyUI ready", OK_COLOR), self.services.message.emit("ComfyUI is ready")))
        self.backend.start_failed.connect(self._start_failed)
        self.backend.gpu_changed.connect(self._gpu)
        self.backend.check_async()
        self.backend.poll_gpu_async()

        geo = services.settings.ui.window_geometry
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- status bar
    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.ai_label = QLabel("● ComfyUI: checking")
        self.ai_button = QToolButton()
        self.ai_button.setText("ComfyUI ▾")
        self.ai_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.ai_button)
        menu.addAction("Start ComfyUI", self.start_comfyui)
        menu.addAction("Check again", self.backend.check_async)
        menu.addAction("Free VRAM (unload models)", lambda: self.services.client.free_memory())
        menu.addAction("Open in browser", lambda: __import__("webbrowser").open(self.services.settings.comfyui.base_url))
        menu.addAction("ComfyUI log", lambda: self.go("Diagnostics"))
        self.ai_button.setMenu(menu)
        self.gpu_label = QLabel("GPU: –")
        self.gpu_label.setStyleSheet(f"color: {MUTED};")
        self.queue_label = QLabel("Queue: idle")
        self.job_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setRange(0, 1000)
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_current)
        log_btn = QPushButton("View Log")
        log_btn.clicked.connect(lambda: LogDialog(self).exec())
        for w in (self.ai_label, self.ai_button, self.gpu_label, self.queue_label):
            sb.addWidget(w)
        for w in (self.job_label, self.progress, self.cancel_btn, log_btn):
            sb.addPermanentWidget(w)

    def _set_ai(self, text: str, color: str) -> None:
        self.ai_label.setText(f"● {text}")
        self.ai_label.setStyleSheet(f"color: {color};")
        dash = self.pages.get("Dashboard")
        if isinstance(dash, DashboardPage):
            dash.set_backend(text)

    def _backend_status(self, st) -> None:
        if self.backend.is_starting:
            return
        if self.services.providers.mock:
            self._set_ai("Demo mode (mock AI)", WARN_COLOR)
        elif st.reachable:
            extra = f" · queue {st.queue_running + st.queue_pending}" if (st.queue_running or st.queue_pending) else ""
            vram = f" · {st.vram_free_gb}/{st.vram_total_gb} GB free" if st.vram_total_gb else ""
            self._set_ai(f"ComfyUI {st.version or ''} connected{vram}{extra}", OK_COLOR)
        else:
            self._set_ai(f"ComfyUI not running ({st.url})", ERROR_COLOR)

    def _gpu(self, gpus) -> None:
        if not gpus:
            self.gpu_label.setText("GPU: no NVIDIA GPU detected")
            return
        g = gpus[0]
        util = f" · {g.utilization_pct}%" if g.utilization_pct is not None else ""
        self.gpu_label.setText(f"GPU: {g.name} · VRAM {g.vram_used_mb / 1024:.1f}/{g.vram_total_gb} GB{util}")

    def _start_failed(self, err: ErrorReport) -> None:
        self._set_ai("ComfyUI failed to start", ERROR_COLOR)
        self.show_error(err)

    def start_comfyui(self) -> None:
        self.backend.start_comfyui()

    # -------------------------------------------------------------------- jobs
    def _job_updated(self, job) -> None:
        pending = self.services.jobs.pending_count()
        self.queue_label.setText(f"Queue: {pending} job(s)" if pending else "Queue: idle")
        if job.state == JobState.RUNNING:
            self.progress.setVisible(True)
            self.cancel_btn.setVisible(True)
            self.progress.setValue(int(job.progress * 1000))
            self.job_label.setText(f"{job.title}: {job.message}"[:120])

    def _job_finished(self, job) -> None:
        self.services.handle_finished(job)
        pending = self.services.jobs.pending_count()
        self.queue_label.setText(f"Queue: {pending} job(s)" if pending else "Queue: idle")
        if pending == 0:
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
        state = {JobState.DONE: "finished", JobState.FAILED: "FAILED", JobState.CANCELLED: "cancelled"}.get(job.state, "")
        self.job_label.setText(f"{job.title}: {state}"[:120])

    def _cancel_current(self) -> None:
        job = self.services.jobs.current
        if job is not None:
            job.on_cancel = lambda: self.services.client.interrupt() if not self.services.providers.mock else None
            self.services.jobs.cancel(job.id)

    # ------------------------------------------------------------------ errors
    def show_error(self, error) -> None:
        if isinstance(error, ErrorReport):
            report = error
        elif isinstance(error, BaseException):
            report = ErrorReport.from_exception(error)
        else:
            report = ErrorReport(str(error))
        log.error("Shown to user: %s | %s", report.message, report.hint)
        ErrorDialog(report, self, on_diagnostic=lambda: self.go("Diagnostics", run=True)).exec()

    # -------------------------------------------------------------- navigation
    def go(self, name: str, run: bool = False) -> None:
        if name in NAV:
            self.nav.setCurrentRow(NAV.index(name))
            if run and isinstance(self.pages.get(name), DiagnosticsPage):
                self.pages[name].run()

    def _project_changed(self, project) -> None:
        self.project_label.setText(f"Project: {project.info.name}" if project else "No project")

    def run_setup_wizard(self) -> None:
        from pixel_rpg_studio.ui.setup_wizard import SetupWizard

        SetupWizard(self.services, self).exec()

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI teardown
        try:
            self.services.settings.ui.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
            self.services.save_settings()
        except Exception:  # noqa: BLE001
            log.exception("Could not save window state")
        self.backend.shutdown()
        self.services.shutdown()
        super().closeEvent(event)
