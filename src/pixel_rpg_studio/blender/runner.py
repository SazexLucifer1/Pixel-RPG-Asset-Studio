"""Run Blender as an external, headless processing engine.

The app writes a job JSON, runs ``blender --background --factory-startup
--python studio_blender.py -- job.json`` and reads the JSON report back.
Blender output is appended to ``logs/blender.log``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pixel_rpg_studio.blender.detect import BlenderInfo, find_blender
from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.errors import BlenderError, JobCancelledError

log = logging.getLogger(__name__)

SCRIPT_NAME = "studio_blender.py"


def script_path() -> Path:
    return paths.blender_scripts_dir() / SCRIPT_NAME


class BlenderRunner:
    def __init__(self, configured_executable: str = "", timeout_s: float = 1800.0) -> None:
        self.configured_executable = configured_executable
        self.timeout_s = timeout_s
        self._info: BlenderInfo | None = None
        self.log_path = paths.logs_dir() / "blender.log"

    def info(self) -> BlenderInfo:
        if self._info is None:
            self._info = find_blender(self.configured_executable)
        if self._info is None:
            raise BlenderError(
                "Blender was not found.",
                hint="Install Blender 3.6 LTS or newer (blender.org, free) and/or select blender.exe in Settings → Blender.",
                code="blender_missing",
            )
        if not self._info.supported:
            raise BlenderError(
                f"Blender {self._info.version_str} is too old.",
                hint="Install Blender 3.6 LTS or newer and select it in Settings.",
                code="blender_too_old",
            )
        return self._info

    def run_job(
        self,
        job: dict[str, Any],
        work_dir: Path | None = None,
        progress: Callable[[float | None, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        expected_frames: int = 0,
    ) -> dict[str, Any]:
        exe = self.info().executable
        work_dir = Path(work_dir or tempfile.mkdtemp(prefix="studio_blender_"))
        work_dir.mkdir(parents=True, exist_ok=True)
        job = dict(job)
        job_path = work_dir / f"job_{job.get('mode', 'job')}_{int(time.time() * 1000)}.json"
        report_path = job_path.with_name(job_path.stem + "_report.json")
        job["report_path"] = str(report_path)
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        cmd = [str(exe), "--background", "--factory-startup", "--python", str(script_path()), "--", str(job_path)]
        log.info("Running Blender job %s", job_path)
        kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "text": True,
                        "encoding": "utf-8", "errors": "replace"}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            raise BlenderError("Blender could not be started.", hint="Check the Blender path in Settings.", details=str(exc)) from exc

        output_lines: list[str] = []
        frames_done = [0]

        def reader() -> None:
            assert proc.stdout is not None
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as logf:
                logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
                for line in proc.stdout:
                    logf.write(line)
                    output_lines.append(line.rstrip())
                    if line.startswith("Saved:") or "Saved: '" in line:
                        frames_done[0] += 1
                        if progress and expected_frames:
                            progress(min(0.99, frames_done[0] / expected_frames), f"Rendered frame {frames_done[0]}/{expected_frames}")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        start = time.time()
        while proc.poll() is None:
            if cancelled and cancelled():
                proc.kill()
                raise JobCancelledError()
            if time.time() - start > self.timeout_s:
                proc.kill()
                raise BlenderError(
                    f"Blender did not finish within {int(self.timeout_s)} s.",
                    hint="Increase the Blender timeout in Settings, or reduce frames/resolution.",
                    details="\n".join(output_lines[-80:]),
                    code="blender_timeout",
                )
            time.sleep(0.2)
        t.join(timeout=5)
        tail = "\n".join(output_lines[-80:])
        if not report_path.exists():
            raise BlenderError(
                f"Blender exited (code {proc.returncode}) without producing a result.",
                hint="See the Blender log for details. A crash here is often caused by an outdated GPU driver or a broken model file.",
                details=tail,
                code="blender_crashed",
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not report.get("ok"):
            raise BlenderError(
                "Blender processing failed: " + "; ".join(report.get("errors", ["unknown error"])),
                hint="Check that the model file is valid. You can open the generated .blend file in Blender to inspect it.",
                details=(report.get("traceback") or "") + "\n" + tail,
                code="blender_failed",
            )
        for w in report.get("warnings", []):
            log.warning("Blender: %s", w)
        return report
