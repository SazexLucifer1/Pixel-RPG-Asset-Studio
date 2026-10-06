"""Dashboard, Projects, Style, AI Models, Settings and Diagnostics pages."""

from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio import APP_NAME, __version__
from pixel_rpg_studio.comfyui.launcher import apply_install_to_settings, detect_install
from pixel_rpg_studio.core import paths
from pixel_rpg_studio.core.config import COMFYUI_INSTALL_TYPES, LOG_LEVELS, VRAM_PROFILES
from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.imaging import pixel
from pixel_rpg_studio.models.catalog import check_models, configured_filename, load_catalog
from pixel_rpg_studio.project.asset_types import all_asset_types
from pixel_rpg_studio.project.style import (
    DOWNSCALE_METHODS,
    LIGHT_DIRECTIONS,
    OUTLINE_STYLES,
    PERSPECTIVES,
    PROPORTIONS,
    SHADING_STYLES,
    SHADOW_STYLES,
    Palette,
    normalize_hex,
)
from pixel_rpg_studio.system.diagnostics import DiagnosticReport, run_full_diagnostic
from pixel_rpg_studio.ui.theme import STATUS_COLORS
from pixel_rpg_studio.ui.widgets.common import button, page_header
from pixel_rpg_studio.ui.widgets.dialogs import LogDialog, open_path, open_url
from pixel_rpg_studio.ui.widgets.image_view import PixelImageView


def _scroll(widget: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(widget)
    return s


# =========================================================================== dashboard
class DashboardPage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        lay = QVBoxLayout(self)
        lay.addWidget(page_header(APP_NAME, "Local-AI pixel-art asset production for Godot. Everything runs on your PC – no paid services."))
        grid = QGridLayout()
        self.project_box = QGroupBox("Project")
        pl = QVBoxLayout(self.project_box)
        self.project_label = QLabel()
        self.project_label.setWordWrap(True)
        pl.addWidget(self.project_label)
        pl.addWidget(button("Open Projects", lambda: main_window.go("Projects")))
        self.backend_box = QGroupBox("Local AI")
        bl = QVBoxLayout(self.backend_box)
        self.backend_label = QLabel("Checking ComfyUI...")
        self.backend_label.setWordWrap(True)
        bl.addWidget(self.backend_label)
        row = QHBoxLayout()
        row.addWidget(button("Start ComfyUI", main_window.start_comfyui))
        row.addWidget(button("Run Diagnostic", lambda: main_window.go("Diagnostics", run=True)))
        bl.addLayout(row)
        self.assets_box = QGroupBox("Assets in this project")
        self.assets_layout = QGridLayout(self.assets_box)
        grid.addWidget(self.project_box, 0, 0)
        grid.addWidget(self.backend_box, 0, 1)
        grid.addWidget(self.assets_box, 1, 0, 1, 2)
        lay.addLayout(grid)
        steps = QGroupBox("Getting started")
        sl = QVBoxLayout(steps)
        for text in (
            "1. Projects → create a project for your game (one folder per game).",
            "2. Style → set sprite size, perspective, lighting, outline and palette; add a few reference images.",
            "3. Characters → describe a character and press 'Run Full Pipeline' (or run the stages one by one).",
            "4. Review the animation preview, regenerate single frames if needed, then 'Export to Godot'.",
            "Missing something? The Diagnostics page explains exactly what is not installed and how to fix it.",
        ):
            l = QLabel(text)
            l.setWordWrap(True)
            sl.addWidget(l)
        lay.addWidget(steps)
        lay.addStretch(1)
        services.project_changed.connect(lambda _p: self.refresh())
        services.assets_changed.connect(lambda _t: self.refresh())
        self.refresh()

    def set_backend(self, text: str) -> None:
        self.backend_label.setText(text)

    def refresh(self) -> None:
        p = self.services.project
        while self.assets_layout.count():
            w = self.assets_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        if p is None:
            self.project_label.setText("No project open.")
            return
        self.project_label.setText(f"<b>{p.info.name}</b><br>{p.root}<br>Sprite {p.style.sprite_width}×{p.style.sprite_height}, "
                                   f"tiles {p.style.tile_size}px, {PERSPECTIVES.get(p.style.perspective, ('?',))[0]}")
        for i, (key, count) in enumerate(p.summary().items()):
            t = next(t for t in all_asset_types() if t.key == key)
            self.assets_layout.addWidget(QLabel(f"{t.label}: <b>{count}</b>"), i // 4, i % 4)


# ============================================================================ projects
class ProjectsPage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("Projects", "One project per game. A project holds the global style, all assets and their generation history."))
        new_box = QGroupBox("New project")
        f = QFormLayout(new_box)
        self.name = QLineEdit()
        self.name.setPlaceholderText("My RPG")
        self.parent_dir = QLineEdit(str(services.settings.projects_dir()))
        prow = QHBoxLayout()
        prow.addWidget(self.parent_dir)
        prow.addWidget(button("…", self._pick_parent))
        self.desc = QLineEdit()
        f.addRow("Name", self.name)
        f.addRow("Location", prow)
        f.addRow("Description", self.desc)
        f.addRow(button("Create Project", self.create, primary=True))
        lay.addWidget(new_box)
        recent_box = QGroupBox("Recent projects")
        rl = QVBoxLayout(recent_box)
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda item: self.open(item.text()))
        rl.addWidget(self.recent)
        row = QHBoxLayout()
        row.addWidget(button("Open Selected", lambda: self.recent.currentItem() and self.open(self.recent.currentItem().text())))
        row.addWidget(button("Open Folder…", self.open_dialog))
        row.addWidget(button("Show in Explorer", lambda: services.project and open_path(services.project.root)))
        row.addStretch(1)
        rl.addLayout(row)
        lay.addWidget(recent_box, 1)
        godot_box = QGroupBox("Godot project (export target for the open project)")
        gf = QFormLayout(godot_box)
        self.godot_dir = QLineEdit()
        grow = QHBoxLayout()
        grow.addWidget(self.godot_dir)
        grow.addWidget(button("…", self._pick_godot))
        self.godot_sub = QLineEdit("assets/generated")
        self.write_res = QCheckBox("Write Godot resources (.tres SpriteFrames / TileSet)")
        self.write_helpers = QCheckBox("Write helper scripts (directional sprite)")
        gf.addRow("Godot folder", grow)
        gf.addRow("Sub folder", self.godot_sub)
        gf.addRow(self.write_res)
        gf.addRow(self.write_helpers)
        gf.addRow(button("Save Godot Settings", self.save_godot))
        exp_row = QHBoxLayout()
        exp_row.addWidget(button("Export All Accepted Assets to Godot", self.export_all, primary=True,
                                 tooltip="Exports every asset marked 'accepted' in one go (asks before overwriting)"))
        exp_row.addWidget(button("Game-ready Export to Project Folder", self.export_to_project_folder,
                                 tooltip="Writes the same files into <project>/exports/godot - copy that folder into any Godot project"))
        exp_row.addStretch(1)
        gf.addRow(exp_row)
        lay.addWidget(godot_box)
        services.project_changed.connect(lambda _p: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self.recent.clear()
        for r in self.services.settings.ui.recent_projects:
            self.recent.addItem(r)
        p = self.services.project
        if p:
            self.godot_dir.setText(p.info.godot.project_dir)
            self.godot_sub.setText(p.info.godot.export_subdir)
            self.write_res.setChecked(p.info.godot.write_resources)
            self.write_helpers.setChecked(p.info.godot.write_helper_scripts)

    def _pick_parent(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Projects location", self.parent_dir.text())
        if d:
            self.parent_dir.setText(d)

    def _pick_godot(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Godot project folder")
        if d:
            self.godot_dir.setText(d)

    def create(self) -> None:
        try:
            self.services.create_project(Path(self.parent_dir.text()), self.name.text(), self.desc.text())
        except StudioError as exc:
            self.main_window.show_error(exc)
            return
        except OSError as exc:
            self.main_window.show_error(exc)
            return
        self.name.clear()
        self.main_window.go("Style")

    def open(self, path: str) -> None:
        try:
            self.services.open_project(path)
        except StudioError as exc:
            self.main_window.show_error(exc)
            return
        self.main_window.go("Dashboard")

    def open_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Open project folder (contains project.json)", str(self.services.settings.projects_dir()))
        if d:
            self.open(d)

    def _accepted(self):
        from pixel_rpg_studio.ui.export_ui import exportable

        p = self.services.project
        if p is None:
            self.main_window.show_error(StudioError("Open a project first."))
            return None
        assets = exportable(p.list_assets(), accepted_only=True)
        if not assets:
            self.main_window.show_error(StudioError("No accepted assets with results to export.",
                                                    hint="Press 'Accept' on finished assets in the studios first."))
            return None
        return assets

    def export_all(self) -> None:
        from pixel_rpg_studio.ui.export_ui import export_assets

        assets = self._accepted()
        if assets:
            self.save_godot()
            export_assets(self, self.services, assets, self.main_window.show_error)

    def export_to_project_folder(self) -> None:
        from pixel_rpg_studio.ui.export_ui import export_assets

        assets = self._accepted()
        if assets:
            target = self.services.project.exports_dir / "godot"
            target.mkdir(parents=True, exist_ok=True)
            if export_assets(self, self.services, assets, self.main_window.show_error, target=target):
                open_path(target)

    def save_godot(self) -> None:
        p = self.services.project
        if p is None:
            self.main_window.show_error(StudioError("Open a project first."))
            return
        p.info.godot.project_dir = self.godot_dir.text().strip()
        p.info.godot.export_subdir = self.godot_sub.text().strip() or "assets/generated"
        p.info.godot.write_resources = self.write_res.isChecked()
        p.info.godot.write_helper_scripts = self.write_helpers.isChecked()
        p.save()
        self.services.message.emit("Godot settings saved")


# =============================================================================== style
class StylePage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.addWidget(page_header("Style", "The project's global style. Every asset uses it, which is what keeps a whole game consistent."))
        top = QHBoxLayout()
        res = QGroupBox("Resolution")
        rf = QFormLayout(res)
        self.sw, self.sh = QSpinBox(), QSpinBox()
        for s in (self.sw, self.sh):
            s.setRange(4, 1024)
            s.setSuffix(" px")
        self.tile = QSpinBox()
        self.tile.setRange(4, 256)
        self.tile.setSuffix(" px")
        self.ppu = QSpinBox()
        self.ppu.setRange(1, 512)
        self.render_scale = QSpinBox()
        self.render_scale.setRange(1, 32)
        self.render_scale.setSuffix("×")
        self.render_scale.setToolTip("Blender renders at sprite size × this factor, then the image is reduced deterministically.")
        rf.addRow("Sprite width", self.sw)
        rf.addRow("Sprite height", self.sh)
        rf.addRow("Tile size", self.tile)
        rf.addRow("Pixel density (px per unit)", self.ppu)
        rf.addRow("Render oversampling", self.render_scale)
        cam = QGroupBox("Camera & lighting")
        cf = QFormLayout(cam)
        self.persp = QComboBox()
        for k, v in PERSPECTIVES.items():
            self.persp.addItem(v[0], k)
        self.elev = QDoubleSpinBox()
        self.elev.setRange(-1, 90)
        self.elev.setSpecialValueText("from perspective")
        self.elev.setSuffix("°")
        self.light = QComboBox()
        for k, v in LIGHT_DIRECTIONS.items():
            self.light.addItem(v[0], k)
        self.shading = QComboBox()
        self.shading.addItems(SHADING_STYLES)
        self.bands = QSpinBox()
        self.bands.setRange(1, 8)
        self.shadow = QComboBox()
        self.shadow.addItems(SHADOW_STYLES)
        cf.addRow("Perspective", self.persp)
        cf.addRow("Camera angle", self.elev)
        cf.addRow("Light direction", self.light)
        cf.addRow("Shading style", self.shading)
        cf.addRow("Shading bands", self.bands)
        cf.addRow("Shadow", self.shadow)
        look = QGroupBox("Look")
        lf = QFormLayout(look)
        self.outline = QComboBox()
        self.outline.addItems(OUTLINE_STYLES)
        self.contrast = QDoubleSpinBox()
        self.contrast.setRange(0.3, 3.0)
        self.contrast.setSingleStep(0.05)
        self.saturation = QDoubleSpinBox()
        self.saturation.setRange(0.0, 3.0)
        self.saturation.setSingleStep(0.05)
        self.max_colors = QSpinBox()
        self.max_colors.setRange(2, 256)
        self.use_palette = QCheckBox("Force project palette")
        self.dither = QCheckBox("Ordered dithering")
        self.aa = QCheckBox("Allow anti-aliasing (not recommended)")
        self.downscale = QComboBox()
        self.downscale.addItems(DOWNSCALE_METHODS)
        self.proportions = QComboBox()
        self.proportions.addItems(list(PROPORTIONS))
        self.bg = QComboBox()
        self.bg.addItems(["transparent", "keep"])
        for label, w in (("Outline", self.outline), ("Contrast", self.contrast), ("Saturation", self.saturation),
                         ("Max colours", self.max_colors), ("", self.use_palette), ("", self.dither), ("", self.aa),
                         ("Downscale method", self.downscale), ("Proportions", self.proportions), ("Background", self.bg)):
            lf.addRow(label, w)
        for b in (res, cam, look):
            top.addWidget(b)
        lay.addLayout(top)
        prompts = QGroupBox("Prompt fragments (added to every generation)")
        pf = QFormLayout(prompts)
        self.pos = QPlainTextEdit()
        self.pos.setFixedHeight(60)
        self.neg = QPlainTextEdit()
        self.neg.setFixedHeight(60)
        pf.addRow("Positive", self.pos)
        pf.addRow("Negative", self.neg)
        lay.addWidget(prompts)
        mid = QHBoxLayout()
        pal_box = QGroupBox("Palette")
        pal_l = QVBoxLayout(pal_box)
        self.palette_name = QLineEdit()
        self.palette_text = QPlainTextEdit()
        self.palette_text.setFixedHeight(80)
        self.palette_text.setPlaceholderText("#000000, #ffffff, ...")
        self.swatches = QLabel()
        pal_l.addWidget(self.palette_name)
        pal_l.addWidget(self.palette_text)
        pal_l.addWidget(self.swatches)
        prow = QHBoxLayout()
        prow.addWidget(button("Extract from References", self.extract_palette))
        prow.addWidget(button("Import .hex/.gpl…", self.import_palette))
        pal_l.addLayout(prow)
        ref_box = QGroupBox("Style references")
        rl = QVBoxLayout(ref_box)
        self.refs = QListWidget()
        self.refs.currentItemChanged.connect(self._show_ref)
        self.ref_view = PixelImageView(min_size=120)
        rl.addWidget(self.refs)
        rl.addWidget(self.ref_view)
        rrow = QHBoxLayout()
        rrow.addWidget(button("Add…", self.add_refs))
        rrow.addWidget(button("Remove", self.remove_ref))
        rrow.addWidget(button("Edit rules.md", lambda: services.project and open_path(services.project.style_dir / "rules.md")))
        rl.addLayout(rrow)
        mid.addWidget(pal_box, 1)
        mid.addWidget(ref_box, 1)
        lay.addLayout(mid)
        lay.addWidget(button("Save Style", self.save, primary=True))
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(content))
        services.project_changed.connect(lambda _p: self.load())
        self.load()

    def load(self) -> None:
        p = self.services.project
        self.setEnabled(p is not None)
        if p is None:
            return
        s = p.style
        self.sw.setValue(s.sprite_width)
        self.sh.setValue(s.sprite_height)
        self.tile.setValue(s.tile_size)
        self.ppu.setValue(s.pixels_per_unit)
        self.render_scale.setValue(s.render_scale)
        self.persp.setCurrentIndex(max(0, self.persp.findData(s.perspective)))
        self.elev.setValue(s.camera_elevation_deg if s.camera_elevation_deg is not None else -1)
        self.light.setCurrentIndex(max(0, self.light.findData(s.lighting_direction)))
        self.shading.setCurrentText(s.shading_style)
        self.bands.setValue(s.shading_bands)
        self.shadow.setCurrentText(s.shadow_style)
        self.outline.setCurrentText(s.outline)
        self.contrast.setValue(s.contrast)
        self.saturation.setValue(s.saturation)
        self.max_colors.setValue(s.max_colors)
        self.use_palette.setChecked(s.use_palette)
        self.dither.setChecked(s.dithering)
        self.aa.setChecked(s.anti_aliasing)
        self.downscale.setCurrentText(s.downscale_method)
        self.proportions.setCurrentText(s.proportions)
        self.bg.setCurrentText(s.background_treatment)
        self.pos.setPlainText(s.positive_prompt)
        self.neg.setPlainText(s.negative_prompt)
        self.palette_name.setText(p.palette.name)
        self.palette_text.setPlainText(", ".join(p.palette.colors))
        self._swatches(p.palette.colors)
        self.refs.clear()
        for r in p.style_references():
            item = QListWidgetItem(r.name)
            item.setData(Qt.ItemDataRole.UserRole, str(r))
            self.refs.addItem(item)

    def _swatches(self, colors: list[str]) -> None:
        cells = "".join(f'<span style="background:{c}; color:{c};">██</span>' for c in colors[:64])
        self.swatches.setText(cells)

    def _parse_palette(self) -> list[str]:
        raw = self.palette_text.toPlainText().replace("\n", ",").replace(" ", ",")
        return [normalize_hex(c) for c in raw.split(",") if c.strip()]

    def save(self) -> None:
        p = self.services.project
        if p is None:
            return
        s = p.style
        try:
            colors = self._parse_palette()
        except ValueError as exc:
            self.main_window.show_error(StudioError("The palette contains an invalid colour.", hint="Use hex colours like #1a2b3c.", details=str(exc)))
            return
        s.sprite_width, s.sprite_height, s.tile_size = self.sw.value(), self.sh.value(), self.tile.value()
        s.pixels_per_unit, s.render_scale = self.ppu.value(), self.render_scale.value()
        s.perspective = self.persp.currentData()
        s.camera_elevation_deg = None if self.elev.value() < 0 else self.elev.value()
        s.lighting_direction = self.light.currentData()
        s.shading_style, s.shading_bands, s.shadow_style = self.shading.currentText(), self.bands.value(), self.shadow.currentText()
        s.outline, s.contrast, s.saturation = self.outline.currentText(), self.contrast.value(), self.saturation.value()
        s.max_colors, s.use_palette, s.dithering, s.anti_aliasing = self.max_colors.value(), self.use_palette.isChecked(), self.dither.isChecked(), self.aa.isChecked()
        s.downscale_method, s.proportions, s.background_treatment = self.downscale.currentText(), self.proportions.currentText(), self.bg.currentText()
        s.positive_prompt, s.negative_prompt = self.pos.toPlainText().strip(), self.neg.toPlainText().strip()
        if colors:
            p.palette = Palette(self.palette_name.text().strip() or "Palette", colors)
        try:
            p.save()
        except StudioError as exc:
            self.main_window.show_error(exc)
            return
        self._swatches(p.palette.colors)
        self.services.message.emit("Style saved")

    def extract_palette(self) -> None:
        p = self.services.project
        refs = p.style_references() if p else []
        if not refs:
            self.main_window.show_error(StudioError("Add style reference images first."))
            return
        colors = pixel.extract_palette([Image.open(r) for r in refs], self.max_colors.value())
        from pixel_rpg_studio.project.style import rgb_to_hex

        self.palette_text.setPlainText(", ".join(rgb_to_hex(c) for c in colors))
        self.palette_name.setText("Extracted from references")
        self._swatches([rgb_to_hex(c) for c in colors])

    def import_palette(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import palette", "", "Palettes (*.hex *.gpl *.txt)")
        if not path:
            return
        colors = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("#Palette", "GIMP", "Name", "Columns", "//")) or (line.startswith("#") and len(line) not in (4, 7)):
                continue
            parts = line.split()
            try:
                if len(parts) >= 3 and all(x.isdigit() for x in parts[:3]):
                    colors.append("#%02x%02x%02x" % tuple(int(x) for x in parts[:3]))
                else:
                    colors.append(normalize_hex(parts[0]))
            except ValueError:
                continue
        if colors:
            self.palette_text.setPlainText(", ".join(colors))
            self.palette_name.setText(Path(path).stem)
            self._swatches(colors)

    def add_refs(self) -> None:
        p = self.services.project
        if p is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Add style references", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        for f in files:
            try:
                p.add_style_reference(Path(f))
            except StudioError as exc:
                self.main_window.show_error(exc)
        self.load()

    def remove_ref(self) -> None:
        item = self.refs.currentItem()
        if item and self.services.project:
            self.services.project.remove_style_reference(Path(item.data(Qt.ItemDataRole.UserRole)))
            self.load()

    def _show_ref(self, item, _prev=None) -> None:
        self.ref_view.set_image(item.data(Qt.ItemDataRole.UserRole) if item else None)


# ============================================================================== models
class _Bridge(QObject):
    done = Signal(object)


class ModelsPage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        self.entries, self.tools = load_catalog()
        self.bridge = _Bridge()
        self.bridge.done.connect(self._fill)
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("AI Models", "Installed / missing models, their location and license. Models are never downloaded automatically "
                                  "and never stored in the app's repository – download them yourself from the official pages."))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Model", "Role", "Status", "File (configurable)", "Location", "License"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._show_details)
        lay.addWidget(self.table, 2)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.details)
        row = QHBoxLayout()
        row.addWidget(button("Refresh", self.refresh, primary=True))
        row.addWidget(button("Open Download Page", self._open_download))
        row.addWidget(button("Open License", self._open_license))
        row.addWidget(button("Choose Installed File…", self._choose_file))
        row.addWidget(button("Open Models Folder", self._open_models_folder))
        row.addStretch(1)
        lay.addLayout(row)
        wf_box = QGroupBox("Workflows and their requirements")
        wl = QVBoxLayout(wf_box)
        self.workflows = QTreeWidget()
        self.workflows.setHeaderLabels(["Workflow", "Requirements"])
        self.workflows.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        wl.addWidget(self.workflows)
        lay.addWidget(wf_box, 1)
        self.statuses = []
        self.refresh()

    def refresh(self) -> None:
        settings = self.services.settings
        client = self.services.client

        def work():
            from pixel_rpg_studio.comfyui.launcher import models_dir

            up = client.is_alive()
            statuses = check_models(settings, self.entries, models_dir(settings), client if up else None)
            self.bridge.done.emit((statuses, up))

        threading.Thread(target=work, daemon=True).start()
        self.workflows.clear()
        for wf in self.services.library.all():
            item = QTreeWidgetItem([wf.title, "; ".join(wf.requirements)])
            item.setToolTip(1, wf.description)
            self.workflows.addTopLevelItem(item)

    def _fill(self, result) -> None:
        statuses, up = result
        self.statuses = statuses
        self.table.setRowCount(len(statuses))
        for r, s in enumerate(statuses):
            vals = [s.entry.name, s.entry.role, s.state, s.configured_filename, s.location or ("(start ComfyUI to check)" if s.installed is None else ""),
                    s.entry.license + (" ⚠" if s.entry.license_warning else "")]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 2:
                    item.setForeground(QColor(STATUS_COLORS.get({"Installed": "OK", "Missing": "Missing"}.get(v, "Info"))))
                self.table.setItem(r, c, item)
        self.table.resizeColumnsToContents()
        self.details.setText("Select a model for details." + ("" if up else "  ComfyUI is not running – status comes from the models folder (if configured)."))

    def _current(self):
        r = self.table.currentRow()
        return self.statuses[r] if 0 <= r < len(self.statuses) else None

    def _show_details(self) -> None:
        s = self._current()
        if s is None:
            return
        e = s.entry
        text = (f"<b>{e.name}</b> – role <code>{e.role}</code>, folder <code>models/{e.folder}</code>, ~{e.size_gb} GB, "
                f"min. VRAM {e.min_vram_gb} GB.<br>{e.notes}<br>License: {e.license}")
        if e.license_warning:
            text += f"<br><span style='color:#f5b942'><b>License warning:</b> {e.license_warning}</span>"
        if s.installed is False:
            text += "<br><span style='color:#f5b942'>Required model missing.</span> Download it from the official page and place it in " \
                    f"ComfyUI's models/{e.folder} folder, or choose an installed alternative file."
        self.details.setText(text)

    def _open_download(self) -> None:
        s = self._current()
        if s:
            open_url(s.entry.download_page)

    def _open_license(self) -> None:
        s = self._current()
        if s:
            open_url(s.entry.license_url or s.entry.download_page)

    def _choose_file(self) -> None:
        s = self._current()
        if s is None:
            return
        from PySide6.QtWidgets import QInputDialog

        options = s.available_alternatives or []
        if options:
            choice, ok = QInputDialog.getItem(self, "Choose model file", f"Installed files in models/{s.entry.folder}:", options, 0, False)
        else:
            choice, ok = QInputDialog.getText(self, "Model file name", f"File name inside models/{s.entry.folder}:", text=s.configured_filename)
        if ok and choice:
            self.services.settings.models.roles[s.entry.role] = choice
            self.services.apply_settings()
            self.refresh()

    def _open_models_folder(self) -> None:
        from pixel_rpg_studio.comfyui.launcher import models_dir

        d = models_dir(self.services.settings)
        if d and d.exists():
            open_path(d)
        else:
            self.main_window.show_error(StudioError("The ComfyUI models folder is unknown.", hint="Select your ComfyUI folder in Settings."))


# ============================================================================ settings
class SettingsPage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.addWidget(page_header("Settings", f"Stored outside the project and repository: {paths.app_data_dir()}"))
        c = QGroupBox("ComfyUI (local image / 3D AI)")
        cf = QFormLayout(c)
        self.comfy_dir = QLineEdit()
        crow = QHBoxLayout()
        crow.addWidget(self.comfy_dir)
        crow.addWidget(button("Choose Folder…", self._pick_comfy))
        crow.addWidget(button("Choose .exe…", self._pick_comfy_exe))
        self.comfy_type = QComboBox()
        self.comfy_type.addItems(COMFYUI_INSTALL_TYPES)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.auto_start = QCheckBox("Start ComfyUI automatically when the app starts")
        self.extra_args = QLineEdit()
        self.extra_args.setPlaceholderText("e.g. --preview-method auto")
        self.start_timeout = QSpinBox()
        self.start_timeout.setRange(10, 3600)
        self.start_timeout.setSuffix(" s")
        self.gen_timeout = QSpinBox()
        self.gen_timeout.setRange(30, 36000)
        self.gen_timeout.setSuffix(" s")
        self.detected = QLabel("")
        self.detected.setObjectName("Muted")
        self.detected.setWordWrap(True)
        for label, w in (("Install folder", crow), ("Install type", self.comfy_type), ("Host", self.host), ("Port", self.port),
                         ("", self.auto_start), ("Extra launch arguments", self.extra_args), ("Start timeout", self.start_timeout),
                         ("Generation timeout", self.gen_timeout), ("", self.detected)):
            cf.addRow(label, w)
        lay.addWidget(c)
        b = QGroupBox("Blender (3D processing and rendering)")
        bf = QFormLayout(b)
        self.blender = QLineEdit()
        self.blender.setPlaceholderText("auto-detect")
        brow = QHBoxLayout()
        brow.addWidget(self.blender)
        brow.addWidget(button("Choose blender.exe…", self._pick_blender))
        self.blender_timeout = QSpinBox()
        self.blender_timeout.setRange(30, 36000)
        self.blender_timeout.setSuffix(" s")
        bf.addRow("Executable", brow)
        bf.addRow("Timeout", self.blender_timeout)
        lay.addWidget(b)
        pth = QGroupBox("Folders")
        pf = QFormLayout(pth)
        self.projects_dir = QLineEdit()
        self.export_dir = QLineEdit()
        self.godot_dir = QLineEdit()
        self.models_dir = QLineEdit()
        self.models_dir.setPlaceholderText("derived from the ComfyUI folder")
        for label, w in (("Default projects folder", self.projects_dir), ("Default export folder", self.export_dir),
                         ("Default Godot project", self.godot_dir), ("ComfyUI models folder (override)", self.models_dir)):
            r = QHBoxLayout()
            r.addWidget(w)
            r.addWidget(button("…", lambda _=False, target=w: self._pick_dir(target)))
            pf.addRow(label, r)
        lay.addWidget(pth)
        g = QGroupBox("GPU / performance")
        gf = QFormLayout(g)
        self.vram = QComboBox()
        self.vram.addItems(VRAM_PROFILES)
        self.vram.setToolTip("auto = choose from detected VRAM. 'low' enables ComfyUI --lowvram (CPU offloading) and smaller resolutions.")
        self.unload = QCheckBox("Unload AI models between stages (frees VRAM for the next model)")
        self.sequential = QCheckBox("Run stages strictly one after another")
        self.sequential.setEnabled(False)
        self.sequential.setToolTip("Always on: the job queue runs one stage at a time.")
        gf.addRow("VRAM profile", self.vram)
        gf.addRow(self.unload)
        gf.addRow(self.sequential)
        lay.addWidget(g)
        a = QGroupBox("Providers and logging")
        af = QFormLayout(a)
        from pixel_rpg_studio.providers.registry import IMAGE_PROVIDERS, RENDERER_PROVIDERS, THREED_PROVIDERS

        self.image_provider, self.threed_provider, self.renderer_provider = QComboBox(), QComboBox(), QComboBox()
        for combo, table in ((self.image_provider, IMAGE_PROVIDERS), (self.threed_provider, THREED_PROVIDERS), (self.renderer_provider, RENDERER_PROVIDERS)):
            for key, (label, _f) in table.items():
                combo.addItem(label, key)
        self.mock = QCheckBox("Demo / test mode: use mock providers (no AI, no Blender – for trying the UI)")
        self.log_level = QComboBox()
        self.log_level.addItems(LOG_LEVELS)
        af.addRow("Image generation", self.image_provider)
        af.addRow("3D generation", self.threed_provider)
        af.addRow("Renderer", self.renderer_provider)
        af.addRow(self.mock)
        af.addRow("Log level", self.log_level)
        af.addRow(button("View Log", lambda: LogDialog(self).exec()))
        lay.addWidget(a)
        rowb = QHBoxLayout()
        rowb.addWidget(button("Save Settings", self.save, primary=True))
        rowb.addWidget(button("Run Setup Wizard", main_window.run_setup_wizard))
        rowb.addStretch(1)
        lay.addLayout(rowb)
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(content))
        services.settings_changed.connect(self.load)
        self.load()

    def load(self) -> None:
        s = self.services.settings
        self.comfy_dir.setText(s.comfyui.install_dir)
        self.comfy_type.setCurrentText(s.comfyui.install_type)
        self.host.setText(s.comfyui.host)
        self.port.setValue(s.comfyui.port)
        self.auto_start.setChecked(s.comfyui.auto_start)
        self.extra_args.setText(s.comfyui.extra_launch_args)
        self.start_timeout.setValue(s.comfyui.start_timeout_s)
        self.gen_timeout.setValue(s.comfyui.generation_timeout_s)
        self.blender.setText(s.blender.executable)
        self.blender_timeout.setValue(s.blender.render_timeout_s)
        self.projects_dir.setText(s.paths.projects_dir or str(s.projects_dir()))
        self.export_dir.setText(s.paths.default_export_dir)
        self.godot_dir.setText(s.paths.godot_project_dir)
        self.models_dir.setText(s.paths.comfyui_models_dir)
        self.vram.setCurrentText(s.gpu.vram_profile)
        self.unload.setChecked(s.gpu.unload_models_between_stages)
        self.sequential.setChecked(True)
        for combo, key in ((self.image_provider, s.providers.image), (self.threed_provider, s.providers.threed), (self.renderer_provider, s.providers.renderer)):
            combo.setCurrentIndex(max(0, combo.findData(key)))
        self.mock.setChecked(s.use_mock_providers)
        self.log_level.setCurrentText(s.log_level.upper())
        self._describe_install()

    def _describe_install(self) -> None:
        inst = detect_install(self.comfy_dir.text().strip()) if self.comfy_dir.text().strip() else None
        if inst:
            self.detected.setText(f"Detected: {inst.describe()}" + (f" – {'; '.join(inst.notes)}" if inst.notes else "")
                                  + ("" if inst.can_autostart else " (cannot be started automatically)"))
        elif self.comfy_dir.text().strip():
            self.detected.setText("No ComfyUI installation recognised in this folder.")
        else:
            self.detected.setText("Not configured – the app will try to auto-detect common install locations.")

    def _pick_comfy(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "ComfyUI folder (Portable: folder with run_nvidia_gpu.bat)")
        if d:
            self._apply_comfy(d)

    def _pick_comfy_exe(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Comfy Desktop / ComfyUI program", "", "Programs (*.exe);;All files (*)")
        if f:
            self._apply_comfy(f)

    def _apply_comfy(self, path: str) -> None:
        inst = detect_install(path)
        if inst is None:
            self.comfy_dir.setText(path)
            self.main_window.show_error(StudioError(f"No ComfyUI installation recognised at {path}.",
                                                    hint="Select the ComfyUI_windows_portable folder, a Comfy Desktop installation "
                                                    "(%LOCALAPPDATA%\\Comfy-Desktop\\ComfyUI-Installs\\<name>), a ComfyUI git folder (with main.py) or the ComfyUI program (.exe)."))
        else:
            import copy

            tmp = copy.deepcopy(self.services.settings)
            apply_install_to_settings(inst, tmp)
            self.comfy_dir.setText(tmp.comfyui.install_dir)
            self.comfy_type.setCurrentText(tmp.comfyui.install_type)
            self.port.setValue(tmp.comfyui.port)
        self._describe_install()

    def _pick_blender(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Blender executable", "", "Blender (blender.exe blender);;All files (*)")
        if f:
            self.blender.setText(f)

    def _pick_dir(self, target: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", target.text())
        if d:
            target.setText(d)

    def save(self) -> None:
        s = self.services.settings
        s.comfyui.install_dir = self.comfy_dir.text().strip()
        s.comfyui.install_type = self.comfy_type.currentText()
        s.comfyui.host = self.host.text().strip() or "127.0.0.1"
        s.comfyui.port = self.port.value()
        s.comfyui.auto_start = self.auto_start.isChecked()
        s.comfyui.extra_launch_args = self.extra_args.text().strip()
        s.comfyui.start_timeout_s = self.start_timeout.value()
        s.comfyui.generation_timeout_s = self.gen_timeout.value()
        s.blender.executable = self.blender.text().strip()
        s.blender.render_timeout_s = self.blender_timeout.value()
        s.paths.projects_dir = self.projects_dir.text().strip()
        s.paths.default_export_dir = self.export_dir.text().strip()
        s.paths.godot_project_dir = self.godot_dir.text().strip()
        s.paths.comfyui_models_dir = self.models_dir.text().strip()
        s.gpu.vram_profile = self.vram.currentText()
        s.gpu.unload_models_between_stages = self.unload.isChecked()
        s.providers.image = self.image_provider.currentData()
        s.providers.threed = self.threed_provider.currentData()
        s.providers.renderer = self.renderer_provider.currentData()
        s.use_mock_providers = self.mock.isChecked()
        s.log_level = self.log_level.currentText()
        try:
            self.services.apply_settings()
        except StudioError as exc:
            self.main_window.show_error(exc)
            return
        import logging

        logging.getLogger().setLevel(s.log_level)
        self.services.message.emit("Settings saved")
        self.main_window.backend.check_async()


# ========================================================================= diagnostics
class DiagnosticsPage(QWidget):
    def __init__(self, services, main_window) -> None:
        super().__init__()
        self.services = services
        self.main_window = main_window
        self.report: DiagnosticReport | None = None
        self.bridge = _Bridge()
        self.bridge.done.connect(self._show)
        self.progress_bridge = _Bridge()
        self.progress_bridge.done.connect(lambda m: self.status.setText(m))
        lay = QVBoxLayout(self)
        lay.addWidget(page_header("Diagnostics", "GPU, VRAM, driver, ComfyUI, Blender, Python, models, custom nodes, disk space, paths and backend connection."))
        row = QHBoxLayout()
        self.run_btn = button("Run Full Diagnostic", self.run, primary=True)
        row.addWidget(self.run_btn)
        row.addWidget(button("Export Report…", self.export))
        row.addWidget(button("Copy Report", self.copy))
        row.addWidget(button("View Log", lambda: LogDialog(self).exec()))
        row.addWidget(button("ComfyUI Log", self._comfy_log))
        row.addWidget(button("Blender Log", self._blender_log))
        row.addStretch(1)
        lay.addLayout(row)
        self.status = QLabel("Press 'Run Full Diagnostic'.")
        self.status.setObjectName("Muted")
        lay.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Check", "Status", "Result"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._details)
        lay.addWidget(self.tree, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        lay.addWidget(self.detail, 1)
        self.action_row = QHBoxLayout()
        lay.addLayout(self.action_row)

    def run(self) -> None:
        self.run_btn.setEnabled(False)
        self.status.setText("Running diagnostic...")
        settings, client = self.services.settings, self.services.client

        def work():
            try:
                rep = run_full_diagnostic(settings, client, progress=self.progress_bridge.done.emit)
            except Exception as exc:  # noqa: BLE001
                from pixel_rpg_studio.system.diagnostics import CheckResult

                rep = DiagnosticReport([CheckResult("Diagnostics", "Run", "error", f"Diagnostic failed: {exc}")])
            self.bridge.done.emit(rep)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, report: DiagnosticReport) -> None:
        self.report = report
        self.run_btn.setEnabled(True)
        self.tree.clear()
        cats: dict[str, QTreeWidgetItem] = {}
        for r in report.results:
            parent = cats.get(r.category)
            if parent is None:
                parent = QTreeWidgetItem([r.category, STATUS_LABEL_SAFE(report.worst(r.category)), ""])
                parent.setForeground(1, QColor(STATUS_COLORS.get(report.worst(r.category), "#8a8aa3")))
                self.tree.addTopLevelItem(parent)
                cats[r.category] = parent
            item = QTreeWidgetItem([r.name, r.label, r.summary])
            item.setForeground(1, QColor(STATUS_COLORS.get(r.status, "#8a8aa3")))
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            parent.addChild(item)
        self.tree.expandAll()
        problems = sum(1 for r in report.results if r.status in ("error", "missing"))
        self.status.setText(f"Finished {report.created}: {problems} problem(s) found." if problems else f"Finished {report.created}: everything looks good.")

    def _details(self) -> None:
        items = self.tree.selectedItems()
        while self.action_row.count():
            w = self.action_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not items:
            return
        r = items[0].data(0, Qt.ItemDataRole.UserRole)
        if r is None:
            return
        self.detail.setPlainText("\n\n".join(x for x in (r.summary, ("What to do: " + r.hint) if r.hint else "", r.details) if x))
        for action in r.actions:
            if action.startswith("url:"):
                url = action[4:]
                self.action_row.addWidget(button("Open Download Page", lambda _=False, u=url: open_url(u), tooltip=url))
            elif action == "choose_comfyui":
                self.action_row.addWidget(button("Choose Folder", lambda: self.main_window.go("Settings")))
            elif action == "choose_blender":
                self.action_row.addWidget(button("Choose Blender", lambda: self.main_window.go("Settings")))
            elif action == "start_comfyui":
                self.action_row.addWidget(button("Start ComfyUI", self.main_window.start_comfyui))
            elif action == "open_models":
                self.action_row.addWidget(button("Open AI Models", lambda: self.main_window.go("AI Models")))
            elif action == "choose_projects_dir":
                self.action_row.addWidget(button("Choose Folder", lambda: self.main_window.go("Settings")))
        self.action_row.addStretch(1)

    def export(self) -> None:
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export diagnostic report", str(Path.home() / "pixel_rpg_studio_diagnostic.md"),
                                              "Markdown (*.md);;JSON (*.json)")
        if path:
            Path(path).write_text(self.report.to_json() if path.endswith(".json") else self.report.to_markdown(), encoding="utf-8")
            self.services.message.emit(f"Report saved to {path}")

    def copy(self) -> None:
        if self.report:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(self.report.to_markdown())

    def _comfy_log(self) -> None:
        p = paths.logs_dir() / "comfyui.log"
        LogDialog(self, "ComfyUI log", p.read_text(encoding="utf-8", errors="replace")[-200_000:] if p.exists() else "(no ComfyUI log yet – "
                  "only written when this app starts ComfyUI)").exec()

    def _blender_log(self) -> None:
        p = paths.logs_dir() / "blender.log"
        LogDialog(self, "Blender log", p.read_text(encoding="utf-8", errors="replace")[-200_000:] if p.exists() else "(no Blender log yet)").exec()


def STATUS_LABEL_SAFE(status: str) -> str:
    from pixel_rpg_studio.system.diagnostics import STATUS_LABEL

    return STATUS_LABEL.get(status, status)
