"""Application entry point (``PixelRPGAssetStudio.exe`` / ``python -m pixel_rpg_studio``).

Startup sequence:
1. load configuration (defaults if missing or damaged) and set up logging
2. first run → setup wizard
3. check whether ComfyUI is running; if not and auto-start is configured,
   start it and wait (with a "continue without waiting" option)
4. open the main window, which keeps showing the backend status

Command line:
  --mock          demo/test mode with mock providers (no AI, no Blender)
  --diagnose      print a diagnostic report and exit (use --out FILE to save)
  --self-test     run a headless end-to-end pipeline with mock providers
  --project DIR   open this project on start
  --no-autostart  do not start ComfyUI automatically
  --version       print the version
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import traceback
from pathlib import Path

from pixel_rpg_studio import APP_NAME, __version__

log = logging.getLogger("pixel_rpg_studio")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="PixelRPGAssetStudio", description=f"{APP_NAME} {__version__}")
    p.add_argument("--mock", action="store_true", help="use mock providers (demo / UI testing)")
    p.add_argument("--diagnose", action="store_true", help="run diagnostics, print the report and exit")
    p.add_argument("--out", type=Path, help="file for --diagnose output (.md or .json)")
    p.add_argument("--self-test", action="store_true", help="run a headless end-to-end pipeline with mock providers")
    p.add_argument("--project", type=Path, help="project folder to open")
    p.add_argument("--no-autostart", action="store_true", help="do not start ComfyUI automatically")
    p.add_argument("--skip-wizard", action="store_true", help="do not show the first-run setup wizard")
    p.add_argument("--version", action="store_true", help="print version and exit")
    # Qt passes its own arguments through; ignore unknown ones.
    args, _unknown = p.parse_known_args(argv)
    return args


def _console_print(text: str) -> None:
    if sys.stdout is not None:
        print(text)


def run_diagnose(settings, out: Path | None) -> int:
    from pixel_rpg_studio.providers.registry import make_client
    from pixel_rpg_studio.system.diagnostics import run_full_diagnostic

    report = run_full_diagnostic(settings, make_client(settings), progress=lambda m: log.info(m))
    text = report.to_json() if (out and out.suffix == ".json") else report.to_markdown()
    if out:
        out.write_text(text, encoding="utf-8")
    _console_print(text)
    return 1 if any(r.status == "error" for r in report.results) else 0


def run_self_test(work_dir: Path | None = None) -> int:
    """Headless end-to-end check: project → character → 3D → render → pixel → sheet → Godot export."""
    from pixel_rpg_studio.core.config import AppSettings
    from pixel_rpg_studio.core.jobs import JobQueue, JobState
    from pixel_rpg_studio.export.godot import plan_asset_export
    from pixel_rpg_studio.pipeline.character import CharacterRunOptions, run_character
    from pixel_rpg_studio.pipeline.common import PipelineContext
    from pixel_rpg_studio.project.project import Project
    from pixel_rpg_studio.providers.registry import build_providers

    work = Path(work_dir or tempfile.mkdtemp(prefix="pixel_rpg_selftest_"))
    settings = AppSettings(use_mock_providers=True)
    project = Project.create(work, "Self Test")
    ctx = PipelineContext(project, build_providers(settings, gpus=[]))
    asset = project.create_asset("character", "Test Hero", "test hero with blue tunic")
    queue = JobQueue()
    job = queue.submit("self-test", lambda c: run_character(ctx, asset, c, CharacterRunOptions(seed=1, animations=["idle", "walk"])))
    job.wait(600)
    queue.shutdown()
    if job.state != JobState.DONE:
        _console_print(f"SELF-TEST FAILED: {job.error.full_text() if job.error else job.state}")
        return 1
    godot = work / "godot_project"
    godot.mkdir()
    (godot / "project.godot").write_text('config_version=5\n[application]\nconfig/name="SelfTest"\n', encoding="utf-8")
    written = plan_asset_export(asset, godot).execute()
    _console_print(f"SELF-TEST OK: {len(written)} files exported to {godot}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        _console_print(f"{APP_NAME} {__version__}")
        return 0

    from pixel_rpg_studio.core.config import load_settings
    from pixel_rpg_studio.core.logging_setup import setup_logging

    settings = load_settings()
    setup_logging(settings.log_level, console=sys.stderr is not None)
    log.info("%s %s starting (python %s, frozen=%s)", APP_NAME, __version__, sys.version.split()[0], getattr(sys, "frozen", False))
    if args.mock:
        settings.use_mock_providers = True
    if args.diagnose:
        return run_diagnose(settings, args.out)
    if args.self_test:
        return run_self_test()
    return run_gui(settings, args)


def run_gui(settings, args) -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

    from pixel_rpg_studio.core import paths
    from pixel_rpg_studio.core.errors import ErrorReport
    from pixel_rpg_studio.ui.main_window import MainWindow
    from pixel_rpg_studio.ui.services import Services
    from pixel_rpg_studio.ui.theme import STYLESHEET
    from pixel_rpg_studio.ui.widgets.dialogs import ErrorDialog

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PixelRPGAssetStudio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    icon = paths.resource_root() / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            ErrorDialog(ErrorReport.from_exception(exc)).exec()
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = excepthook

    problems = settings.validate()
    services = Services(settings)
    if problems:
        log.warning("Settings problems: %s", problems)

    if not settings.setup_completed and not args.skip_wizard:
        from pixel_rpg_studio.ui.setup_wizard import SetupWizard

        SetupWizard(services).exec()
        settings.setup_completed = True
        try:
            services.apply_settings()
        except Exception:  # noqa: BLE001
            log.exception("Could not save settings after setup")

    window = MainWindow(services)

    # Start ComfyUI if configured and not running (never blocks forever).
    if not services.providers.mock and settings.comfyui.auto_start and not args.no_autostart and settings.comfyui.install_type != "remote":
        if not services.client.is_alive():
            from pixel_rpg_studio.comfyui.launcher import resolve_install

            if resolve_install(settings) is not None:
                dlg = QDialog()
                dlg.setWindowTitle(APP_NAME)
                dlg.setModal(True)
                lay = QVBoxLayout(dlg)
                msg = QLabel("Starting local AI backend (ComfyUI)...\nThe first start can take a few minutes.")
                bar = QProgressBar()
                bar.setRange(0, 0)
                skip = QPushButton("Continue without waiting")
                skip.clicked.connect(dlg.accept)
                for w in (msg, bar, skip):
                    lay.addWidget(w)
                window.backend.starting.connect(lambda m: msg.setText(m))
                window.backend.started.connect(dlg.accept)
                window.backend.start_failed.connect(lambda _e: dlg.accept())
                window.backend.start_comfyui()
                dlg.resize(420, 140)
                dlg.exec()

    if args.project:
        try:
            services.open_project(args.project)
        except Exception as exc:  # noqa: BLE001
            window.show_error(exc)
    elif settings.ui.last_project and Path(settings.ui.last_project).exists():
        try:
            services.open_project(settings.ui.last_project)
        except Exception:  # noqa: BLE001
            log.warning("Could not reopen last project %s", settings.ui.last_project, exc_info=True)
    window.show()
    if services.project is None:
        window.go("Projects")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
