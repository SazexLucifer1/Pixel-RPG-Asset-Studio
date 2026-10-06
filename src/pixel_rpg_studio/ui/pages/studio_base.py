"""Base class for all asset studio pages.

Layout: asset list | previews (Original / AI Generated / Processed / Final /
extra tabs) | properties + actions. Subclasses add their form fields and
pipeline actions; listing, previews, history, compare and Godot export are
shared here so no asset type duplicates this code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio.core.errors import ErrorReport, ExportConflictError, StudioError
from pixel_rpg_studio.export.godot import is_godot_project, merge_plans, plan_asset_export
from pixel_rpg_studio.imaging.compare import compare_to_master, side_by_side
from pixel_rpg_studio.project.asset import (
    ROLE_FINAL,
    ROLE_GENERATED,
    ROLE_MASTER,
    ROLE_ORIGINAL,
    ROLE_PROCESSED,
    ROLE_SHEET,
    Asset,
)
from pixel_rpg_studio.project.asset_types import get_asset_type
from pixel_rpg_studio.ui.widgets.common import button, page_header
from pixel_rpg_studio.ui.widgets.dialogs import confirm, open_path
from pixel_rpg_studio.ui.widgets.image_view import PixelImageView

SEED_RANDOM = -1


class SeedSpin(QSpinBox):
    """Seed input; -1 means random."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(-1, 2_147_483_647)
        self.setValue(-1)
        self.setSpecialValueText("Random")
        self.setToolTip("-1 / Random = new seed each time. The seed used is always stored in the metadata.")

    def seed(self) -> int | None:
        return None if self.value() < 0 else self.value()


class AssetStudioPage(QWidget):
    type_keys: tuple[str, ...] = ()
    title = "Studio"
    subtitle = ""

    def __init__(self, services, main_window=None) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        self.asset: Asset | None = None
        self._busy = False

        # ---- left: asset list
        self.type_combo = QComboBox()
        for key in self.type_keys:
            self.type_combo.addItem(get_asset_type(key).label, key)
        self.type_combo.setVisible(len(self.type_keys) > 1)
        self.type_combo.currentIndexChanged.connect(self.refresh_list)
        self.asset_list = QListWidget()
        self.asset_list.currentItemChanged.connect(self._on_select)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.type_combo)
        ll.addWidget(self.asset_list, 1)
        new_row = QHBoxLayout()
        new_row.addWidget(button("New", self.new_asset, primary=True))
        new_row.addWidget(button("Delete", self.delete_asset, danger=True))
        ll.addLayout(new_row)

        # ---- centre: previews
        self.tabs = QTabWidget()
        self.views: dict[str, PixelImageView] = {}
        for role, label in ((ROLE_ORIGINAL, "Original"), (ROLE_GENERATED, "AI Generated"), (ROLE_PROCESSED, "Processed"), (ROLE_FINAL, "Final")):
            v = PixelImageView()
            self.views[role] = v
            self.tabs.addTab(v, label)
        self.extra_tabs()
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.tabs.addTab(self.history, "History / Metadata")
        self.info_label = QLabel("")
        self.info_label.setObjectName("Muted")
        self.info_label.setWordWrap(True)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.tabs, 1)
        cl.addWidget(self.info_label)
        common = QHBoxLayout()
        for b in (
            button("Accept", self.accept_asset, tooltip="Mark the current final result as accepted"),
            button("Edit", self.edit_asset, tooltip="Open the final image in your default image editor"),
            button("Compare", self.compare_asset, tooltip="Compare with the master reference / previous version"),
            button("Open Folder", self.open_folder),
            button("Export to Godot", self.export_asset, primary=True),
        ):
            common.addWidget(b)
        common.addStretch(1)
        cl.addLayout(common)

        # ---- right: properties + actions
        self.form_box = QWidget()
        self.form_layout = QVBoxLayout(self.form_box)
        self.form_layout.setContentsMargins(0, 0, 4, 0)
        self.build_form(self.form_layout)
        self.form_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.form_box)
        scroll.setMinimumWidth(440)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(scroll)
        splitter.setSizes([200, 600, 460])
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(page_header(self.title, self.subtitle))
        layout.addWidget(splitter, 1)

        services.project_changed.connect(lambda _p: self.refresh_list())
        services.assets_changed.connect(self._assets_changed)
        self.refresh_list()

    # ------------------------------------------------------------ overrides
    def extra_tabs(self) -> None:
        """Subclasses add tabs (animation preview, tile grid...)."""

    def build_form(self, layout: QVBoxLayout) -> None:
        """Subclasses add their property widgets and actions."""

    def load_form(self, asset: Asset | None) -> None:
        """Fill form widgets from the selected asset."""

    def update_extra_views(self, asset: Asset) -> None:
        """Refresh subclass previews."""

    # --------------------------------------------------------------- helpers
    @property
    def current_type(self) -> str:
        return self.type_combo.currentData() or (self.type_keys[0] if self.type_keys else "")

    def group(self, title: str, layout: QVBoxLayout) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addWidget(box)
        return form

    def ctx(self):
        return self.services.pipeline_context()

    def show_error(self, error: ErrorReport | StudioError | Exception) -> None:
        if self.main_window is not None:
            self.main_window.show_error(error)
        else:  # pragma: no cover
            QMessageBox.warning(self, "Error", str(error))

    def run_job(self, title: str, func: Callable[[Any], Any], on_done: Callable[[Any], None] | None = None) -> None:
        if self.services.project is None:
            self.show_error(StudioError("No project is open.", hint="Create or open a project on the Projects page first."))
            return
        if self.asset is None and "{asset}" not in title:
            pass
        self.services.submit(title, func, on_done=on_done or (lambda _r: self.reload_asset()), asset_type=self.current_type)
        self.services.message.emit(f"Queued: {title}")

    def require_asset(self) -> Asset | None:
        if self.asset is None:
            self.show_error(StudioError("Select or create an asset first.", hint="Use 'New' at the bottom of the list."))
            return None
        return self.asset

    def pick_image(self, title: str = "Select image") -> Path | None:
        path, _ = QFileDialog.getOpenFileName(self, title, "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        return Path(path) if path else None

    # ------------------------------------------------------------------ list
    def _assets_changed(self, type_key: str) -> None:
        if not type_key or type_key in self.type_keys:
            self.refresh_list(keep=True)

    def refresh_list(self, *_args, keep: bool = True) -> None:
        current_id = self.asset.id if (keep and self.asset) else None
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        project = self.services.project
        if project is not None and self.current_type:
            for asset in project.list_assets(self.current_type):
                item = QListWidgetItem(f"{asset.name}\n{asset.id} · {asset.meta.status}")
                item.setData(Qt.ItemDataRole.UserRole, asset.id)
                thumb = asset.output_path(ROLE_FINAL) or asset.output_path(ROLE_MASTER) or asset.output_path(ROLE_PROCESSED)
                if thumb:
                    from PySide6.QtGui import QIcon, QPixmap

                    from pixel_rpg_studio.ui.widgets.image_view import load_qimage

                    q = load_qimage(thumb)
                    if q is not None:
                        item.setIcon(QIcon(QPixmap.fromImage(q.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio))))
                self.asset_list.addItem(item)
                if asset.id == current_id:
                    self.asset_list.setCurrentItem(item)
        self.asset_list.blockSignals(False)
        if current_id is None or self.asset_list.currentItem() is None:
            if self.asset_list.count():
                self.asset_list.setCurrentRow(0)
            else:
                self.show_asset(None)
        else:
            self.reload_asset()

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None or self.services.project is None:
            self.show_asset(None)
            return
        try:
            asset = self.services.project.get_asset(self.current_type, item.data(Qt.ItemDataRole.UserRole))
        except StudioError as exc:
            self.show_error(exc)
            return
        self.show_asset(asset)

    def reload_asset(self) -> None:
        if self.asset is not None and self.services.project is not None:
            try:
                self.show_asset(Asset.load(self.asset.root))
            except StudioError:
                self.show_asset(None)

    def show_asset(self, asset: Asset | None) -> None:
        self.asset = asset
        for role, view in self.views.items():
            path = asset.output_path(role) if asset else None
            if role == ROLE_FINAL and asset and path is None:
                path = asset.output_path(ROLE_SHEET)
            view.set_image(path, "Not generated yet" if asset else "No asset selected")
        if asset is None:
            self.history.setPlainText("")
            self.info_label.setText("Create a new asset with 'New'." if self.services.project else "Open or create a project first (Projects page).")
            self.load_form(None)
            return
        self.history.setPlainText(self._history_text(asset))
        last = asset.meta.generations[-1] if asset.meta.generations else None
        info = f"{asset.name} · status: {asset.meta.status}"
        if last:
            info += f" · last step: {last.stage} ({last.provider}" + (f", seed {last.seed}" if last.seed is not None else "") + ")"
        self.info_label.setText(info)
        self.load_form(asset)
        self.update_extra_views(asset)

    def _history_text(self, asset: Asset) -> str:
        lines = [f"Asset: {asset.name} ({asset.id})", f"Type: {asset.type}  Status: {asset.meta.status}", f"Folder: {asset.root}", ""]
        for g in reversed(asset.meta.generations[-60:]):
            lines.append(f"[{g.timestamp}] {g.stage} via {g.provider}" + (f"  seed={g.seed}" if g.seed is not None else "")
                         + (f"  target={g.target}" if g.target else ""))
            if g.workflow:
                lines.append(f"    workflow: {g.workflow}   model: {g.model}")
            if g.prompt:
                lines.append(f"    prompt: {g.prompt[:300]}")
            if g.notes:
                lines.append(f"    notes: {g.notes[:300]}")
        lines += ["", "Settings:", json.dumps(asset.meta.settings, indent=2, default=str)[:6000]]
        return "\n".join(lines)

    # --------------------------------------------------------------- actions
    def new_asset(self) -> None:
        project = self.services.project
        if project is None:
            self.show_error(StudioError("No project is open.", hint="Create or open a project on the Projects page first."))
            return
        name, ok = QInputDialog.getText(self, f"New {get_asset_type(self.current_type).label[:-1] if self.current_type != 'vfx' else 'effect'}", "Name:")
        if not ok or not name.strip():
            return
        try:
            asset = project.create_asset(self.current_type, name.strip())
        except StudioError as exc:
            self.show_error(exc)
            return
        self.asset = asset
        self.refresh_list(keep=True)

    def delete_asset(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        if not confirm(self, "Delete asset", f"Delete '{asset.name}' and all its files?\n{asset.root}"):
            return
        try:
            self.services.project.delete_asset(asset)
        except StudioError as exc:
            self.show_error(exc)
        self.asset = None
        self.refresh_list(keep=False)

    def accept_asset(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        if asset.output_path(ROLE_FINAL) is None and asset.output_path(ROLE_SHEET) is None:
            processed = asset.output_path(ROLE_PROCESSED)
            if processed is None:
                self.show_error(StudioError("There is no result to accept yet."))
                return
            asset.set_output(ROLE_FINAL, processed)
        for info in asset.meta.animations.values():
            info.accepted = True
        asset.meta.status = "accepted"
        asset.save()
        self.reload_asset()
        self.refresh_list()

    def edit_asset(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        path = asset.output_path(ROLE_FINAL) or asset.output_path(ROLE_PROCESSED) or asset.output_path(ROLE_GENERATED)
        if path is None:
            self.show_error(StudioError("Nothing to edit yet."))
            return
        open_path(path)
        self.services.message.emit(f"Opened {path.name} in the default editor. Save it, then press Accept.")

    def open_folder(self) -> None:
        if self.asset is not None:
            open_path(self.asset.root)

    def compare_images(self) -> tuple[list[Image.Image], list[str], str]:
        """Images for the compare dialog. Subclasses may override (e.g. selected frame)."""
        asset = self.asset
        imgs, labels = [], []
        master = asset.output_path(ROLE_MASTER)
        for role, label in ((ROLE_MASTER, "Master"), (ROLE_GENERATED, "AI"), (ROLE_PROCESSED, "Processed"), (ROLE_FINAL, "Final")):
            p = asset.output_path(role)
            if p:
                img = Image.open(p).convert("RGBA")
                if role == ROLE_GENERATED:
                    img.thumbnail((128, 128))
                imgs.append(img)
                labels.append(label)
        summary = ""
        final = asset.output_path(ROLE_FINAL)
        if master and final:
            summary = compare_to_master(Image.open(master), Image.open(final)).summary()
        return imgs, labels, summary

    def compare_asset(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        imgs, labels, summary = self.compare_images()
        if not imgs:
            self.show_error(StudioError("Nothing to compare yet."))
            return
        # Bring images to a common height for a fair side-by-side view.
        h = max(i.height for i in imgs)
        scaled = [i if i.height == h else i.resize((max(1, i.width * h // i.height), h), Image.NEAREST) for i in imgs]
        composite = side_by_side(scaled, labels, scale=max(1, 256 // max(1, h)))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Compare – {asset.name}")
        dlg.resize(900, 520)
        view = PixelImageView(dlg)
        view.set_image(composite)
        lbl = QLabel(summary or "Side-by-side comparison")
        lbl.setWordWrap(True)
        lay = QVBoxLayout(dlg)
        lay.addWidget(view, 1)
        lay.addWidget(lbl)
        dlg.exec()

    # ----------------------------------------------------------------- export
    def godot_dir(self, ask: bool = True) -> Path | None:
        project = self.services.project
        configured = (project.info.godot.project_dir if project else "") or self.services.settings.paths.godot_project_dir
        if configured and Path(configured).is_dir():
            return Path(configured)
        if not ask:
            return None
        folder = QFileDialog.getExistingDirectory(self, "Select your Godot project folder (contains project.godot)")
        if not folder:
            return None
        path = Path(folder)
        if not is_godot_project(path) and not confirm(self, "Not a Godot project",
                                                      f"{path} does not contain project.godot.\nExport there anyway?"):
            return None
        if project:
            project.info.godot.project_dir = str(path)
            project.save()
        return path

    def export_asset(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        self.export_assets([asset])

    def export_assets(self, assets: list[Asset]) -> None:
        target = self.godot_dir()
        if target is None:
            return
        project = self.services.project
        g = project.info.godot
        try:
            plan = merge_plans(plan_asset_export(a, target, g.export_subdir, g.write_resources, g.write_helper_scripts) for a in assets)
            try:
                written = plan.execute(overwrite=False)
            except ExportConflictError as conflict:
                if not confirm(self, "Overwrite files?", f"{len(conflict.conflicts)} file(s) already exist in the Godot project. Overwrite them?",
                               details=conflict.details):
                    return
                written = plan.execute(overwrite=True)
        except StudioError as exc:
            self.show_error(exc)
            return
        except OSError as exc:
            self.show_error(exc)
            return
        QMessageBox.information(self, "Exported", f"Exported {len(written)} file(s) to\n{target / g.export_subdir}")


def line(text: str = "", placeholder: str = "") -> QLineEdit:
    w = QLineEdit(text)
    w.setPlaceholderText(placeholder)
    return w


def spin(value: int, lo: int, hi: int, suffix: str = "") -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    return w
