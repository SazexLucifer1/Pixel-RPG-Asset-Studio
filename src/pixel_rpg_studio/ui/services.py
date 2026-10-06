"""Application services shared by all UI pages.

Holds settings, the current project, the job queue and the provider set,
and re-emits worker-thread events as Qt signals (always delivered on the GUI
thread via queued connections).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from pixel_rpg_studio.comfyui.client import ComfyUIClient
from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary
from pixel_rpg_studio.core.config import AppSettings, save_settings
from pixel_rpg_studio.core.errors import ProjectError
from pixel_rpg_studio.core.jobs import Job, JobContext, JobQueue, JobState
from pixel_rpg_studio.pipeline.common import PipelineContext
from pixel_rpg_studio.project.project import Project
from pixel_rpg_studio.providers.registry import ProviderSet, build_providers, make_client

log = logging.getLogger(__name__)


class Services(QObject):
    project_changed = Signal(object)  # Project | None
    settings_changed = Signal()
    job_updated = Signal(object)  # Job
    job_finished = Signal(object)  # Job
    assets_changed = Signal(str)  # asset type key ("" = all)
    message = Signal(str)

    def __init__(self, settings: AppSettings, gpus: list | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.project: Project | None = None
        self.jobs = JobQueue()
        self.jobs.add_listener(self._on_job)
        self._gpus = gpus
        self._callbacks: dict[int, tuple[Callable | None, Callable | None]] = {}
        self.library = WorkflowLibrary()
        self.client: ComfyUIClient = make_client(settings)
        self.providers: ProviderSet = build_providers(settings, self.library, self.client, gpus)
        self.error_handler: Callable[[Any], None] | None = None

    # -------------------------------------------------------------- settings
    def apply_settings(self, settings: AppSettings | None = None, save: bool = True) -> None:
        if settings is not None:
            self.settings = settings
        if save:
            save_settings(self.settings)
        self.client = make_client(self.settings)
        self.library = WorkflowLibrary([self.project.root / "workflows"] if self.project else None)
        self.providers = build_providers(self.settings, self.library, self.client, self._gpus)
        self.settings_changed.emit()

    def save_settings(self) -> None:
        save_settings(self.settings)

    # --------------------------------------------------------------- project
    def open_project(self, path: str | Path) -> Project:
        project = Project.open(Path(path))
        self._set_project(project)
        return project

    def create_project(self, parent: str | Path, name: str, description: str = "") -> Project:
        project = Project.create(Path(parent), name, description)
        self._set_project(project)
        return project

    def _set_project(self, project: Project | None) -> None:
        self.project = project
        if project is not None:
            self.settings.add_recent_project(project.root)
            try:
                self.save_settings()
            except Exception:  # noqa: BLE001
                log.exception("Could not save settings")
            self.library = WorkflowLibrary([project.root / "workflows"])
            self.providers = build_providers(self.settings, self.library, self.client, self._gpus)
        self.project_changed.emit(project)

    def require_project(self) -> Project:
        if self.project is None:
            raise ProjectError("No project is open.", hint="Create or open a project on the Projects page first.")
        return self.project

    def pipeline_context(self) -> PipelineContext:
        return PipelineContext(self.require_project(), self.providers)

    # ------------------------------------------------------------------ jobs
    def submit(self, title: str, func: Callable[[JobContext], Any], on_done: Callable[[Any], None] | None = None,
               on_error: Callable[[Any], None] | None = None, asset_type: str = "") -> Job:
        def wrapped(ctx: JobContext) -> Any:
            return func(ctx)

        job = self.jobs.submit(title, wrapped)
        self._callbacks[job.id] = (on_done, on_error)
        job.asset_type = asset_type  # type: ignore[attr-defined]
        return job

    def _on_job(self, job: Job) -> None:  # worker thread
        self.job_updated.emit(job)
        if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            self.job_finished.emit(job)

    def handle_finished(self, job: Job) -> None:
        """Called on the GUI thread (connected to job_finished)."""
        on_done, on_error = self._callbacks.pop(job.id, (None, None))
        if job.state == JobState.DONE:
            if on_done:
                on_done(job.result)
            self.assets_changed.emit(getattr(job, "asset_type", ""))
        elif job.state == JobState.FAILED:
            self.assets_changed.emit(getattr(job, "asset_type", ""))
            if on_error:
                on_error(job.error)
            elif self.error_handler:
                self.error_handler(job.error)
        elif job.state == JobState.CANCELLED:
            self.message.emit(f"Cancelled: {job.title}")

    def shutdown(self) -> None:
        self.jobs.shutdown()
