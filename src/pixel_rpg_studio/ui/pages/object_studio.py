"""Weapon / Item / Prop / Environment / Building studios (shared implementation)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio.export.godot import VIEW_PREFIX
from pixel_rpg_studio.pipeline import three_d
from pixel_rpg_studio.pipeline.common import build_prompt, generate_concept, import_image, process_image
from pixel_rpg_studio.pipeline.objects import ObjectRunOptions, run_object
from pixel_rpg_studio.project.asset import ROLE_FINAL, ROLE_GENERATED, Asset
from pixel_rpg_studio.project.asset_types import DIRECTIONS, get_asset_type
from pixel_rpg_studio.project.style import PERSPECTIVES
from pixel_rpg_studio.ui.pages.studio_base import AssetStudioPage, SeedSpin, line, spin
from pixel_rpg_studio.ui.widgets.common import button
from pixel_rpg_studio.ui.widgets.image_view import PixelImageView

TOP_DOWN = "Top-down (90°)"


class ObjectStudioPage(AssetStudioPage):
    """Parametrised by ``type_keys``; buildings get extra camera/footprint options."""

    def __init__(self, services, main_window=None, type_key: str = "weapon") -> None:
        self.type_keys = (type_key,)
        t = get_asset_type(type_key)
        self.title = f"{t.label[:-1] if t.label.endswith('s') else t.label} Studio"
        self.subtitle = (t.description + "  Pipeline: reference or text → 3D model (optional) → Blender with fixed camera → "
                         "render views → pixel conversion → PNG.")
        self.is_building = type_key == "building"
        super().__init__(services, main_window)

    def extra_tabs(self) -> None:
        self.views_widget = QWidget()
        self.views_grid = QGridLayout(self.views_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.views_widget)
        self.tabs.insertTab(0, scroll, "Views")
        self.tabs.setCurrentIndex(0)

    def build_form(self, layout: QVBoxLayout) -> None:
        t = get_asset_type(self.type_keys[0])
        f = self.group("Description", layout)
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.description.setPlaceholderText("e.g. rusty iron longsword with leather grip")
        self.subtype = QComboBox()
        self.subtype.addItems(list(t.subtypes) or ["other"])
        self.subtype.setEditable(True)
        self.extra = line(placeholder="materials, colours, details")
        f.addRow("Description", self.description)
        f.addRow("Kind", self.subtype)
        f.addRow("Materials / details" if self.is_building else "Details", self.extra)
        self.reference_path = line(placeholder="optional reference image")
        ref_row = QHBoxLayout()
        ref_row.addWidget(self.reference_path)
        ref_row.addWidget(button("…", self._pick_ref))
        f.addRow("Reference", ref_row)

        o = self.group("Output", layout)
        size_row = QHBoxLayout()
        self.sprite_w = spin(48, 8, 1024, " px")
        self.sprite_h = spin(48, 8, 1024, " px")
        size_row.addWidget(self.sprite_w)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.sprite_h)
        o.addRow("Sprite resolution", size_row)
        if self.is_building:
            fp = QHBoxLayout()
            self.foot_w = spin(3, 1, 32, " tiles")
            self.foot_h = spin(2, 1, 32, " tiles")
            fp.addWidget(self.foot_w)
            fp.addWidget(QLabel("×"))
            fp.addWidget(self.foot_h)
            o.addRow("Footprint", fp)
            o.addRow(button("Sprite width from footprint", self._footprint_size))
        self.perspective = QComboBox()
        self.perspective.addItem("Project style", None)
        for key, (label, *_rest) in PERSPECTIVES.items():
            self.perspective.addItem(label, key)
        self.perspective.addItem(TOP_DOWN, "topdown90")
        o.addRow("Perspective", self.perspective)
        self.elevation = QDoubleSpinBox()
        self.elevation.setRange(-1, 90)
        self.elevation.setSpecialValueText("from perspective")
        self.elevation.setValue(-1)
        self.elevation.setSuffix("°")
        o.addRow("Camera angle", self.elevation)
        views = QGridLayout()
        self.view_checks: dict[str, QCheckBox] = {}
        for i, (key, d) in enumerate(DIRECTIONS.items()):
            cb = QCheckBox(d.label)
            self.view_checks[key] = cb
            views.addWidget(cb, i // 2, i % 2)
        o.addRow("Views", views)
        self.use_3d = QCheckBox("Use 3D (consistent multiple angles)")
        self.use_3d.setChecked(True)
        self.use_3d.setToolTip("Without 3D the processed concept image becomes the sprite (single view).")
        o.addRow(self.use_3d)

        g = self.group("Generation", layout)
        self.seed = SeedSpin()
        self.model_seed = SeedSpin()
        g.addRow("Concept seed", self.seed)
        g.addRow("3D seed", self.model_seed)

        p = self.group("Pipeline", layout)
        p.addRow(button("▶ Run Full Pipeline", self.run_full, primary=True))
        steps = [("1. Generate Concept", self.gen_concept), ("Import Image…", self.import_concept),
                 ("2D: Pixelate Concept", self.pixelate), ("2. Generate 3D Model", self.gen_model),
                 ("Import 3D Model…", self.import_model), ("3. Prepare Model", self.prepare),
                 ("4. Render Views", self.render_views), ("Regenerate (new seed)", self.regenerate)]
        for i in range(0, len(steps), 2):
            row = QHBoxLayout()
            for text, slot in steps[i : i + 2]:
                row.addWidget(button(text, slot))
            p.addRow(row)
        self.report = QLabel("")
        self.report.setWordWrap(True)
        self.report.setObjectName("Muted")
        p.addRow(self.report)

    # ------------------------------------------------------------- form io
    def load_form(self, asset: Asset | None) -> None:
        if asset is None:
            self._fill_views(None)
            return
        style = self.services.project.style
        s = three_d.settings(asset, style)
        self.description.setPlainText(asset.meta.description)
        if asset.meta.subtype:
            self.subtype.setCurrentText(asset.meta.subtype)
        self.extra.setText(s.get("extra", ""))
        self.sprite_w.setValue(int(s.get("sprite_width", 48)))
        self.sprite_h.setValue(int(s.get("sprite_height", 48)))
        for key, cb in self.view_checks.items():
            cb.setChecked(key in s.get("directions", []))
        idx = self.perspective.findData(s.get("perspective_override"))
        self.perspective.setCurrentIndex(max(0, idx))
        self.elevation.setValue(float(s["elevation_deg"]) if s.get("elevation_deg") is not None and not s.get("perspective_override") else -1)
        self.use_3d.setChecked(bool(s.get("use_3d", True)))
        if self.is_building:
            self.foot_w.setValue(int(s.get("footprint", [3, 2])[0]))
            self.foot_h.setValue(int(s.get("footprint", [3, 2])[1]))
        rep = s.get("prepare_report") or {}
        self.report.setText(("Warnings: " + "; ".join(rep["warnings"])) if rep.get("warnings") else "")
        self._fill_views(asset)

    def save_form(self) -> Asset | None:
        asset = self.require_asset()
        if asset is None:
            return None
        s = three_d.settings(asset, self.services.require_project().style)
        asset.meta.description = self.description.toPlainText().strip()
        asset.meta.subtype = self.subtype.currentText().strip()
        dirs = [k for k, cb in self.view_checks.items() if cb.isChecked()] or ["s"]
        persp = self.perspective.currentData()
        elevation, yaw = None, None
        if persp == "topdown90":
            elevation, yaw = 89.9, 0.0
        elif persp:
            elevation, yaw = PERSPECTIVES[persp][1], PERSPECTIVES[persp][2]
        if self.elevation.value() >= 0:
            elevation = self.elevation.value()
        s.update({"extra": self.extra.text().strip(), "sprite_width": self.sprite_w.value(), "sprite_height": self.sprite_h.value(),
                  "directions": dirs, "use_3d": self.use_3d.isChecked(), "perspective_override": persp,
                  "elevation_deg": elevation, "camera_yaw_deg": yaw})
        if self.is_building:
            s["footprint"] = [self.foot_w.value(), self.foot_h.value()]
        asset.save()
        return asset

    def _footprint_size(self) -> None:
        tile = self.services.project.style.tile_size if self.services.project else 16
        self.sprite_w.setValue(self.foot_w.value() * tile)
        self.sprite_h.setValue(int(self.foot_w.value() * tile * 1.25))

    def _pick_ref(self) -> None:
        p = self.pick_image("Reference image")
        if p:
            self.reference_path.setText(str(p))

    def _ref(self) -> Path | None:
        t = self.reference_path.text().strip()
        return Path(t) if t and Path(t).is_file() else None

    def _prompt(self, asset: Asset) -> tuple[str, str]:
        return build_prompt(self.services.project, asset.type, asset.meta.description, asset.meta.subtype, self.extra.text().strip())

    def _fill_views(self, asset: Asset | None) -> None:
        while self.views_grid.count():
            w = self.views_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        if asset is None:
            return
        n = 0
        for role, rel in sorted(asset.meta.outputs.items()):
            if not role.startswith(VIEW_PREFIX):
                continue
            key = role[len(VIEW_PREFIX):]
            box = QVBoxLayout()
            view = PixelImageView(min_size=140)
            view.set_image(asset.root / rel)
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.addWidget(view)
            cl.addWidget(QLabel(DIRECTIONS[key].label if key in DIRECTIONS else key))
            self.views_grid.addWidget(cell, n // 4, n % 4)
            n += 1
        if n == 0:
            self.views_grid.addWidget(QLabel("No rendered views yet. Use 'Run Full Pipeline' or the steps on the right."), 0, 0)

    # -------------------------------------------------------------- actions
    def run_full(self) -> None:
        asset = self.save_form()
        if asset is None:
            return
        s = asset.meta.settings
        opts = ObjectRunOptions(seed=self.seed.seed(), model_seed=self.model_seed.seed(), reference_image=self._ref(),
                                use_3d=self.use_3d.isChecked(), directions=s["directions"])
        self.run_job(f"Full pipeline: {asset.name}", lambda c: run_object(self.ctx(), asset, c, opts))

    def gen_concept(self, seed=None) -> None:
        asset = self.save_form()
        if asset is None:
            return
        prompt, negative = self._prompt(asset)
        seed = self.seed.seed() if seed is None else seed
        ref = self._ref()

        def job(c):
            ctx = self.ctx()
            src = generate_concept(ctx, asset, c, prompt=prompt, negative=negative, seed=seed, reference=ref)
            process_image(ctx, asset, src, width=asset.meta.settings["sprite_width"], height=asset.meta.settings["sprite_height"])

        self.run_job(f"Concept: {asset.name}", job)

    def regenerate(self) -> None:
        self.gen_concept(seed=-1)

    def import_concept(self) -> None:
        asset = self.save_form()
        path = self.pick_image() if asset else None
        if path:
            def job(c):
                src = import_image(asset, path)
                process_image(self.ctx(), asset, src, width=asset.meta.settings["sprite_width"], height=asset.meta.settings["sprite_height"])

            self.run_job(f"Import: {asset.name}", job)

    def pixelate(self) -> None:
        asset = self.save_form()
        if asset is None:
            return

        def job(c):
            src = asset.output_path(ROLE_GENERATED)
            if src is None:
                from pixel_rpg_studio.core.errors import StudioError

                raise StudioError("Generate or import a concept image first.")
            out = process_image(self.ctx(), asset, src, width=asset.meta.settings["sprite_width"], height=asset.meta.settings["sprite_height"])
            asset.set_output(ROLE_FINAL, out)
            asset.save()

        self.run_job(f"Pixelate: {asset.name}", job)

    def gen_model(self) -> None:
        asset = self.save_form()
        if asset:
            seed = self.model_seed.seed()
            self.run_job(f"3D model: {asset.name}", lambda c: three_d.generate_model(self.ctx(), asset, c, seed=seed))

    def import_model(self) -> None:
        asset = self.save_form()
        if asset is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import 3D model", "", "3D models (*.glb *.gltf *.obj *.fbx *.stl *.ply)")
        if path:
            self.run_job(f"Import model: {asset.name}", lambda c: three_d.import_model(asset, Path(path)))

    def prepare(self) -> None:
        asset = self.save_form()
        if asset:
            self.run_job(f"Prepare model: {asset.name}", lambda c: three_d.prepare_model(self.ctx(), asset, c))

    def render_views(self) -> None:
        asset = self.save_form()
        if asset:
            self.run_job(f"Render views: {asset.name}", lambda c: three_d.render_frames(self.ctx(), asset, c))

    def compare_images(self):
        asset = self.asset
        views = [(k[len(VIEW_PREFIX):], asset.root / v) for k, v in sorted(asset.meta.outputs.items()) if k.startswith(VIEW_PREFIX)]
        if len(views) > 1:
            return [Image.open(p).convert("RGBA") for _, p in views], [k for k, _ in views], "All views side by side (consistency check)"
        return super().compare_images()
