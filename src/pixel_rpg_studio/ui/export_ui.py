"""Shared UI flow for exporting assets to Godot (conflict-safe)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from pixel_rpg_studio.core.errors import ExportConflictError, StudioError
from pixel_rpg_studio.export.godot import is_godot_project, merge_plans, plan_asset_export
from pixel_rpg_studio.project.asset import Asset
from pixel_rpg_studio.ui.widgets.dialogs import confirm


def choose_godot_dir(parent: QWidget, services, ask: bool = True) -> Path | None:
    project = services.project
    configured = (project.info.godot.project_dir if project else "") or services.settings.paths.godot_project_dir
    if configured and Path(configured).is_dir():
        return Path(configured)
    if not ask:
        return None
    start = services.settings.paths.default_export_dir or ""
    folder = QFileDialog.getExistingDirectory(parent, "Select your Godot project folder (contains project.godot)", start)
    if not folder:
        return None
    path = Path(folder)
    if not is_godot_project(path) and not confirm(parent, "Not a Godot project", f"{path} does not contain project.godot.\nExport there anyway?"):
        return None
    if project:
        project.info.godot.project_dir = str(path)
        project.save()
    return path


def export_assets(parent: QWidget, services, assets: list[Asset], show_error, target: Path | None = None) -> int:
    """Export assets; asks before overwriting. Returns the number of files written (0 = cancelled/failed)."""
    target = target or choose_godot_dir(parent, services)
    if target is None:
        return 0
    g = services.require_project().info.godot
    try:
        plan = merge_plans(plan_asset_export(a, target, g.export_subdir, g.write_resources, g.write_helper_scripts) for a in assets)
        try:
            written = plan.execute(overwrite=False)
        except ExportConflictError as conflict:
            if not confirm(parent, "Overwrite files?", f"{len(conflict.conflicts)} file(s) already exist in the destination. Overwrite them?",
                           details=conflict.details):
                return 0
            written = plan.execute(overwrite=True)
    except (StudioError, OSError) as exc:
        show_error(exc)
        return 0
    QMessageBox.information(parent, "Exported", f"Exported {len(written)} file(s) to\n{target / g.export_subdir}")
    return len(written)


def exportable(assets: list[Asset], accepted_only: bool = True) -> list[Asset]:
    result = []
    for a in assets:
        if accepted_only and a.meta.status != "accepted":
            continue
        try:
            plan_asset_export(a, Path("."))
        except StudioError:
            continue
        result.append(a)
    return result
