"""Keeps track of the ComfyUI server: periodic health checks and auto-start."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from pixel_rpg_studio.comfyui.client import ServerStatus
from pixel_rpg_studio.comfyui.launcher import ComfyUIProcess, resolve_install
from pixel_rpg_studio.core.errors import ErrorReport, StudioError
from pixel_rpg_studio.system.gpu import query_gpus, resolve_profile

log = logging.getLogger(__name__)


class BackendManager(QObject):
    status_changed = Signal(object)  # ServerStatus
    starting = Signal(str)  # progress message while starting
    start_failed = Signal(object)  # ErrorReport
    started = Signal()
    gpu_changed = Signal(object)  # list[GPUInfo]

    def __init__(self, services, poll_ms: int = 5000) -> None:
        super().__init__()
        self.services = services
        self.process = getattr(services, "comfy_process", None) or ComfyUIProcess()
        self.last_status = ServerStatus(False, services.settings.comfyui.base_url, error="Not checked yet")
        self._busy = threading.Lock()
        self._starting = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_async)
        self.timer.start(poll_ms)
        self.gpu_timer = QTimer(self)
        self.gpu_timer.timeout.connect(self.poll_gpu_async)
        self.gpu_timer.start(max(poll_ms, 5000))

    @property
    def is_starting(self) -> bool:
        return self._starting

    def check_async(self) -> None:
        if not self._busy.acquire(blocking=False):
            return

        def run():
            try:
                st = self.services.client.status()
                self.last_status = st
                self.status_changed.emit(st)
            except Exception:  # noqa: BLE001
                log.exception("status check failed")
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True, name="comfy-status").start()

    def poll_gpu_async(self) -> None:
        threading.Thread(target=lambda: self.gpu_changed.emit(query_gpus(timeout=3)), daemon=True, name="gpu-poll").start()

    def start_comfyui(self) -> None:
        """Start ComfyUI in the background (never blocks the UI)."""
        if self._starting:
            return
        settings = self.services.settings
        install = resolve_install(settings)
        if install is None:
            self.start_failed.emit(ErrorReport(
                "No ComfyUI installation configured or detected.",
                hint="Choose your ComfyUI folder in Settings (Portable: the folder containing run_nvidia_gpu.bat; "
                "Desktop: ComfyUI.exe). Or start ComfyUI yourself and press 'Check again'.",
                code="comfyui_missing"))
            return
        self._starting = True

        def run():
            try:
                if self.services.client.is_alive():
                    self.started.emit()
                    return
                profile = resolve_profile(settings.gpu.vram_profile)
                self.starting.emit(f"Starting ComfyUI ({install.kind})...")
                self.process.start(install, settings, low_vram=profile.comfy_low_vram_flag)
                self.process.wait_until_ready(self.services.client.is_alive, settings.comfyui.start_timeout_s,
                                              progress=self.starting.emit)
                self.started.emit()
            except StudioError as exc:
                self.start_failed.emit(ErrorReport(exc.message, exc.hint, exc.details, exc.code))
            except Exception as exc:  # noqa: BLE001
                self.start_failed.emit(ErrorReport.from_exception(exc))
            finally:
                self._starting = False
                self.check_async()

        threading.Thread(target=run, daemon=True, name="comfy-start").start()

    def shutdown(self) -> None:
        self.timer.stop()
        self.gpu_timer.stop()
        if self.process.running:
            self.process.stop()
