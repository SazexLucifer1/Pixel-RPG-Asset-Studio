"""System diagnostics used by the setup wizard, the Diagnostics page and ``--diagnose``.

Every check returns a :class:`CheckResult` that says what was found, why it
matters and what to do. Checks never raise.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from pixel_rpg_studio import APP_NAME, __version__
from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.config import AppSettings

OK, WARN, ERROR, MISSING, INFO = "ok", "warning", "error", "missing", "info"
STATUS_LABEL = {OK: "OK", WARN: "Warning", ERROR: "Error", MISSING: "Missing", INFO: "Info"}


@dataclass
class CheckResult:
    category: str
    name: str
    status: str
    summary: str
    details: str = ""
    hint: str = ""
    actions: list[str] = field(default_factory=list)  # e.g. "choose_comfyui", "url:https://..."

    @property
    def label(self) -> str:
        return STATUS_LABEL.get(self.status, self.status)


@dataclass
class DiagnosticReport:
    results: list[CheckResult]
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    app_version: str = __version__

    def by_category(self, category: str) -> list[CheckResult]:
        return [r for r in self.results if r.category == category]

    def worst(self, category: str) -> str:
        order = [OK, INFO, WARN, MISSING, ERROR]
        statuses = [r.status for r in self.by_category(category)] or [INFO]
        return max(statuses, key=order.index)

    def to_json(self) -> str:
        return json.dumps({"created": self.created, "app_version": self.app_version, "results": [asdict(r) for r in self.results]}, indent=2)

    def to_markdown(self) -> str:
        lines = [f"# {APP_NAME} diagnostic report", "", f"- Created: {self.created}", f"- App version: {self.app_version}", ""]
        current = None
        for r in self.results:
            if r.category != current:
                current = r.category
                lines += ["", f"## {current}", "", "| Check | Status | Result |", "|---|---|---|"]
            lines.append(f"| {r.name} | {r.label} | {r.summary.replace('|', '/')} |")
        lines += ["", "## Details", ""]
        for r in self.results:
            if r.details or r.hint:
                lines.append(f"### {r.category} / {r.name}")
                if r.hint:
                    lines.append(f"What to do: {r.hint}")
                if r.details:
                    lines += ["```", r.details.strip(), "```"]
                lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------------------- checks
def check_os() -> CheckResult:
    system = platform.system()
    if system == "Windows":
        ver = sys.getwindowsversion()  # type: ignore[attr-defined]
        build = ver.build
        name = "Windows 11" if build >= 22000 else ("Windows 10" if ver.major == 10 else f"Windows {ver.major}.{ver.minor}")
        status = OK if ver.major >= 10 else WARN
        return CheckResult("System", "Windows version", status, f"{name} (build {build})",
                           hint="" if status == OK else "Windows 10 or 11 (64-bit) is required.")
    return CheckResult("System", "Operating system", INFO, f"{system} {platform.release()}",
                       hint="The application is built for Windows first; other systems are supported for development.")


def check_runtime() -> CheckResult:
    import numpy
    import PIL
    import PySide6

    details = f"Python {sys.version}\nPySide6 {PySide6.__version__}\nPillow {PIL.__version__}\nnumpy {numpy.__version__}\nFrozen build: {paths.is_frozen()}"
    ok = sys.version_info >= (3, 10)
    return CheckResult("System", "Python runtime", OK if ok else ERROR, f"Python {platform.python_version()} ({'bundled' if paths.is_frozen() else 'source'})",
                       details=details, hint="" if ok else "Python 3.10+ is required.")


def check_gpu(gpus=None) -> list[CheckResult]:
    from pixel_rpg_studio.system.gpu import find_nvidia_smi, profile_for_vram, query_gpus

    gpus = query_gpus() if gpus is None else gpus
    if not gpus:
        smi = find_nvidia_smi()
        return [CheckResult("Hardware", "NVIDIA GPU", MISSING if smi is None else WARN,
                            "No NVIDIA GPU detected" if smi is None else "nvidia-smi found but reported no GPU",
                            hint="Local AI generation needs an NVIDIA GPU with a current driver (RTX 3060 12 GB recommended). "
                            "Without it, ComfyUI runs very slowly on the CPU or not at all.",
                            actions=["url:https://www.nvidia.com/Download/index.aspx"])]
    g = max(gpus, key=lambda x: x.vram_total_mb)
    vram_gb = g.vram_total_gb
    profile = profile_for_vram(g.vram_total_mb)
    vram_status = OK if vram_gb >= 8 else (WARN if vram_gb >= 6 else ERROR)
    return [
        CheckResult("Hardware", "NVIDIA GPU", OK, g.name, details="\n".join(f"{x.name}: {x.vram_total_mb} MB" for x in gpus)),
        CheckResult("Hardware", "VRAM", vram_status, f"{vram_gb} GB → profile '{profile.label}'", details=profile.description,
                    hint="" if vram_status == OK else "Use Low VRAM mode (Settings) and smaller resolutions."),
        CheckResult("Hardware", "NVIDIA driver", OK if g.driver_version else WARN, g.driver_version or "unknown",
                    hint="" if g.driver_version else "Install a current NVIDIA Studio/Game Ready driver."),
    ]


def check_disk(settings: AppSettings) -> list[CheckResult]:
    results = []
    targets = {"Projects folder": settings.projects_dir(), "Application data": paths.app_data_dir()}
    if settings.comfyui.install_dir:
        targets["ComfyUI folder"] = Path(settings.comfyui.install_dir)
    seen = set()
    for name, path in targets.items():
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError as exc:
            results.append(CheckResult("Storage", f"Disk space ({name})", WARN, f"Cannot read disk usage: {exc}"))
            continue
        key = (usage.total, usage.free)
        if key in seen:
            continue
        seen.add(key)
        free_gb = usage.free / 1024**3
        status = OK if free_gb >= 30 else (WARN if free_gb >= 5 else ERROR)
        results.append(CheckResult("Storage", f"Disk space ({name})", status, f"{free_gb:.1f} GB free on {probe.anchor or probe}",
                                   hint="" if status == OK else "AI models need ~10-20 GB; generated projects need a few GB. Free some space."))
    return results


def check_writable(settings: AppSettings) -> list[CheckResult]:
    results = []
    for name, path in (("Application data", paths.app_data_dir()), ("Logs", paths.logs_dir()), ("Projects folder", settings.projects_dir())):
        ok = paths.is_writable_dir(path)
        results.append(CheckResult("Storage", f"Writable: {name}", OK if ok else ERROR, str(path),
                                   hint="" if ok else "Choose another folder in Settings (e.g. inside Documents).",
                                   actions=[] if ok else ["choose_projects_dir"]))
    return results


def check_comfyui(settings: AppSettings, client=None) -> list[CheckResult]:
    from pixel_rpg_studio.comfyui.launcher import resolve_install

    results = []
    install = resolve_install(settings)
    if settings.comfyui.install_type == "remote":
        results.append(CheckResult("ComfyUI", "Installation", INFO, "Using an already running ComfyUI (remote mode)"))
    elif install is None:
        results.append(CheckResult("ComfyUI", "Installation", MISSING, "ComfyUI installation not found",
                                   hint="Install ComfyUI (Portable or Desktop) and select its folder. Both are free.",
                                   actions=["choose_comfyui", "url:https://github.com/comfyanonymous/ComfyUI/releases", "url:https://www.comfy.org/download"]))
    else:
        status = OK if install.can_autostart else WARN
        results.append(CheckResult("ComfyUI", "Installation", status, install.describe(), details="\n".join(install.notes),
                                   hint="" if status == OK else "Automatic start is not possible; start ComfyUI manually.",
                                   actions=["choose_comfyui"]))
    st = client.status() if client is not None else None
    if st is not None and st.reachable:
        results.append(CheckResult("ComfyUI", "Server", OK, f"Running at {st.url} (version {st.version or 'unknown'})",
                                   details=json.dumps(st.devices, indent=2)))
    else:
        results.append(CheckResult("ComfyUI", "Server", ERROR if install else MISSING,
                                   f"Not reachable at {settings.comfyui.base_url}",
                                   details=st.error if st else "",
                                   hint="Press 'Start ComfyUI'. If you use ComfyUI Desktop, its default port is 8000 - check Settings.",
                                   actions=["start_comfyui"]))
    return results


def check_workflows(settings: AppSettings, library=None, client=None, server_up: bool = False) -> list[CheckResult]:
    from pixel_rpg_studio.comfyui.workflows import WorkflowLibrary

    library = library or WorkflowLibrary()
    results = []
    object_info = None
    if client is not None and server_up:
        try:
            object_info = client.object_info(refresh=True)
        except Exception as exc:  # noqa: BLE001
            results.append(CheckResult("Workflows", "Node list", WARN, "Could not read node list from ComfyUI", details=str(exc)))
    for name in library.names():
        try:
            wf = library.get(name)
        except Exception as exc:  # noqa: BLE001
            results.append(CheckResult("Workflows", name, ERROR, "Invalid workflow file", details=getattr(exc, "details", "") or str(exc)))
            continue
        if object_info is None:
            results.append(CheckResult("Workflows", wf.title, INFO, "File OK (start ComfyUI to check nodes and models)"))
            continue
        rep = wf.validate_against_server(object_info, settings.models.roles)
        if rep.ok:
            results.append(CheckResult("Workflows", wf.title, OK, "Ready"))
        else:
            hint_parts = []
            if rep.missing_node_types:
                hint_parts.append("Update ComfyUI or install the custom nodes providing: " + ", ".join(rep.missing_node_types))
            if rep.missing_models:
                hint_parts.append("Install the missing models (AI Models page) or select installed alternatives.")
            results.append(CheckResult("Workflows", wf.title, MISSING if not rep.errors else ERROR, rep.summary(),
                                       details="\n".join(wf.requirements), hint=" ".join(hint_parts), actions=["open_models"]))
    return results


def check_models(settings: AppSettings, client=None, server_up: bool = False) -> list[CheckResult]:
    from pixel_rpg_studio.comfyui.launcher import models_dir
    from pixel_rpg_studio.models.catalog import check_models as run_check
    from pixel_rpg_studio.models.catalog import load_catalog

    entries, _ = load_catalog()
    statuses = run_check(settings, entries, models_dir(settings), client if server_up else None)
    results = []
    for s in statuses:
        if s.installed:
            results.append(CheckResult("AI Models", s.entry.name, OK, f"{s.configured_filename} ({s.source})", details=s.location))
        elif s.installed is None:
            results.append(CheckResult("AI Models", s.entry.name, INFO, "Unknown - start ComfyUI or set the models folder",
                                       actions=["url:" + s.entry.download_page]))
        else:
            results.append(CheckResult("AI Models", s.entry.name, MISSING, f"Required model missing: {s.configured_filename}",
                                       details=f"Folder: models/{s.entry.folder}\nLicense: {s.entry.license}\n{s.entry.license_warning}\n{s.entry.notes}",
                                       hint=f"Download it from the official page and place it in ComfyUI's models/{s.entry.folder} folder, "
                                       "or choose an installed alternative on the AI Models page.",
                                       actions=["url:" + s.entry.download_page, "open_models"]))
    return results


def check_blender(settings: AppSettings) -> CheckResult:
    from pixel_rpg_studio.blender.detect import MIN_VERSION, find_blender

    info = find_blender(settings.blender.executable)
    if info is None:
        return CheckResult("Blender", "Blender", MISSING, "Blender not found",
                           hint=f"Install Blender {MIN_VERSION[0]}.{MIN_VERSION[1]} LTS or newer (free) or select blender.exe.",
                           actions=["choose_blender", "url:https://www.blender.org/download/"])
    if not info.supported:
        return CheckResult("Blender", "Blender", WARN, f"Blender {info.version_str} is too old", details=str(info.executable),
                           hint="Install Blender 3.6 LTS or newer.", actions=["choose_blender"])
    return CheckResult("Blender", "Blender", OK, f"Blender {info.version_str}", details=str(info.executable), actions=["choose_blender"])


def check_paths(settings: AppSettings) -> CheckResult:
    from pixel_rpg_studio.core.config import settings_path

    lines = [
        f"Settings file: {settings_path()}",
        f"Logs: {paths.logs_dir()}",
        f"Projects: {settings.projects_dir()}",
        f"ComfyUI: {settings.comfyui.install_dir or '(auto-detect)'} [{settings.comfyui.install_type}] {settings.comfyui.base_url}",
        f"ComfyUI models override: {settings.paths.comfyui_models_dir or '(none)'}",
        f"Blender: {settings.blender.executable or '(auto-detect)'}",
        f"Godot project: {settings.paths.godot_project_dir or '(not set)'}",
        f"Workflows (bundled): {paths.bundled_workflows_dir()}",
    ]
    return CheckResult("Configuration", "Configured paths", INFO, f"Settings in {paths.app_data_dir()}", details="\n".join(lines))


def run_full_diagnostic(settings: AppSettings, client=None, progress: Callable[[str], None] | None = None) -> DiagnosticReport:
    say = progress or (lambda m: None)
    results: list[CheckResult] = []
    steps: list[tuple[str, Callable[[], object]]] = [
        ("Checking system", lambda: [check_os(), check_runtime()]),
        ("Checking GPU", lambda: check_gpu()),
        ("Checking storage", lambda: check_disk(settings) + check_writable(settings)),
        ("Checking ComfyUI", lambda: check_comfyui(settings, client)),
    ]
    for label, fn in steps:
        say(label)
        try:
            out = fn()
            results.extend(out if isinstance(out, list) else [out])
        except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
            results.append(CheckResult("Diagnostics", label, ERROR, f"Check crashed: {exc}"))
    server_up = any(r.category == "ComfyUI" and r.name == "Server" and r.status == OK for r in results)
    for label, fn in (
        ("Checking workflows", lambda: check_workflows(settings, client=client, server_up=server_up)),
        ("Checking AI models", lambda: check_models(settings, client, server_up)),
        ("Checking Blender", lambda: [check_blender(settings)]),
        ("Collecting configuration", lambda: [check_paths(settings)]),
    ):
        say(label)
        try:
            results.extend(fn())
        except Exception as exc:  # noqa: BLE001
            results.append(CheckResult("Diagnostics", label, ERROR, f"Check crashed: {exc}"))
    return DiagnosticReport(results)


def summary_table(report: DiagnosticReport) -> list[tuple[str, str, str]]:
    """The compact status table shown by the setup wizard (component, status, explanation)."""
    rows = []

    def row(label: str, category: str, name: str | None = None):
        items = [r for r in report.by_category(category) if name is None or r.name == name]
        if not items:
            return
        order = [OK, INFO, WARN, MISSING, ERROR]
        worst = max(items, key=lambda r: order.index(r.status))
        rows.append((label, worst.label, worst.summary))

    row("GPU", "Hardware", "NVIDIA GPU")
    row("VRAM", "Hardware", "VRAM")
    row("ComfyUI", "ComfyUI")
    row("Blender", "Blender")
    image = [r for r in report.by_category("AI Models") if "Hunyuan" not in r.name]
    threed = [r for r in report.by_category("AI Models") if "Hunyuan" in r.name]
    for label, items in (("Image AI", image), ("3D AI", threed)):
        if items:
            order = [OK, INFO, WARN, MISSING, ERROR]
            worst = max(items, key=lambda r: order.index(r.status))
            rows.append((label, worst.label, worst.summary))
    rows.append(("Pixel tools", "OK", "Built-in deterministic processing"))
    row("Storage", "Storage")
    return rows
