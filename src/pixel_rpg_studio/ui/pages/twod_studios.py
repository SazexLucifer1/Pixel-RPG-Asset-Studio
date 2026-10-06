"""2D-first studios: Tiles/Tilesets, Backgrounds, VFX and the Sprite Sheet builder."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.export.godot import LAYER_PREFIX
from pixel_rpg_studio.imaging import tiles as T
from pixel_rpg_studio.imaging.vfx import DIRECTIONS as VFX_DIRECTIONS
from pixel_rpg_studio.imaging.vfx import PRESETS, VFXParams
from pixel_rpg_studio.pipeline import backgrounds as BG
from pixel_rpg_studio.pipeline import sheets as SH
from pixel_rpg_studio.pipeline import tiles as TP
from pixel_rpg_studio.pipeline import vfx as VX
from pixel_rpg_studio.project.asset import Asset
from pixel_rpg_studio.spritesheet.builder import animated_preview
from pixel_rpg_studio.ui.pages.studio_base import AssetStudioPage, SeedSpin, line, spin
from pixel_rpg_studio.ui.widgets.animation_preview import AnimationPreview
from pixel_rpg_studio.ui.widgets.common import button
from pixel_rpg_studio.ui.widgets.image_view import PixelImageView


# =========================================================================== tiles
class TileStudioPage(AssetStudioPage):
    type_keys = ("tileset", "tile")
    title = "Tile / Tileset Studio"
    subtitle = ("Structured tilesets, not random squares: two seamless terrains → 16 corner transitions + variants → "
                "Godot TileSet with a terrain set (autotile). The seam test repeats the tile so seams are visible immediately.")

    def extra_tabs(self) -> None:
        self.grid_view = PixelImageView()
        self.map_view = PixelImageView()
        self.atlas_view = PixelImageView()
        self.tabs.insertTab(0, self._with_label(self.grid_view, "seam"), "Seamless Test")
        self.tabs.insertTab(1, self.map_view, "Connection Preview")
        self.tabs.insertTab(2, self.atlas_view, "Tileset Atlas")
        self.tabs.setCurrentIndex(0)

    def _with_label(self, view, key) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(view, 1)
        self.seam_label = QLabel("")
        lay.addWidget(self.seam_label)
        row = QHBoxLayout()
        self.seam_slot = QComboBox()
        self.seam_slot.addItems(["base", "overlay"])
        self.seam_slot.currentIndexChanged.connect(lambda _i: self.reload_asset())
        self.seam_gaps = QCheckBox("Show tile borders")
        self.seam_gaps.toggled.connect(lambda _b: self.reload_asset())
        row.addWidget(QLabel("Terrain"))
        row.addWidget(self.seam_slot)
        row.addWidget(self.seam_gaps)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def build_form(self, layout: QVBoxLayout) -> None:
        f = self.group("Terrains", layout)
        self.base_terrain = QComboBox()
        self.base_terrain.setEditable(True)
        self.base_terrain.addItems(list(TP.TERRAIN_PRESETS))
        self.overlay_terrain = QComboBox()
        self.overlay_terrain.setEditable(True)
        self.overlay_terrain.addItems(list(TP.TERRAIN_PRESETS))
        self.overlay_terrain.setCurrentText("dirt")
        self.base_desc = line(placeholder="extra description, e.g. lush, small flowers")
        f.addRow("Base terrain", self.base_terrain)
        f.addRow("Overlay terrain", self.overlay_terrain)
        f.addRow("Details", self.base_desc)
        self.seamless = QCheckBox("Force seamless (deterministic)")
        self.seamless.setChecked(True)
        f.addRow(self.seamless)
        g = self.group("Generation", layout)
        self.seed = SeedSpin()
        self.variants = spin(4, 0, 16)
        self.rough = QDoubleSpinBox()
        self.rough.setRange(0.0, 1.0)
        self.rough.setSingleStep(0.05)
        self.rough.setValue(0.35)
        g.addRow("Seed", self.seed)
        g.addRow("Variants per terrain", self.variants)
        g.addRow("Edge roughness", self.rough)
        p = self.group("Pipeline", layout)
        p.addRow(button("▶ Generate Complete Tileset", self.run_full, primary=True))
        for a, b in ((("Generate Base Tile", lambda: self.gen_tile("base")), ("Import Base Tile…", lambda: self.import_tile("base"))),
                     (("Generate Overlay Tile", lambda: self.gen_tile("overlay")), ("Import Overlay Tile…", lambda: self.import_tile("overlay"))),
                     (("Build Transitions", self.build_set), ("Regenerate Set (new seed)", lambda: self.build_set(new_seed=True)))):
            row = QHBoxLayout()
            row.addWidget(button(a[0], a[1]))
            row.addWidget(button(b[0], b[1]))
            p.addRow(row)
        note = QLabel("Single 'Tiles' assets only need the base tile. Tilesets use both terrains.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        p.addRow(note)

    def load_form(self, asset: Asset | None) -> None:
        if asset is None:
            return
        terr = asset.meta.settings.get("terrains", {})
        if terr.get("base"):
            self.base_terrain.setCurrentText(terr["base"])
        if terr.get("overlay"):
            self.overlay_terrain.setCurrentText(terr["overlay"])

    def update_extra_views(self, asset: Asset) -> None:
        slot = self.seam_slot.currentText() if hasattr(self, "seam_slot") else "base"
        tile_path = asset.path("tiles", f"{slot}.png")
        if tile_path.exists():
            tile = Image.open(tile_path)
            self.grid_view.set_image(T.tile_grid(tile, 4, 4, gap=1 if self.seam_gaps.isChecked() else 0))
            sc = T.seam_score(tile)
            verdict = "seamless" if sc["score"] <= T.SEAM_THRESHOLD else "VISIBLE SEAM"
            self.seam_label.setText(f"Seam score {sc['score']:.2f} (horizontal {sc['horizontal']:.2f}, vertical {sc['vertical']:.2f}) → {verdict}. "
                                    "≤ 1.0 means the wrap edge is no stronger than edges already inside the tile.")
        else:
            self.grid_view.set_image(None, "No tile yet")
            self.seam_label.setText("")
        self.map_view.set_image(asset.output_path("tileset_preview"), "Build transitions to preview connections")
        self.atlas_view.set_image(asset.output_path("tileset_atlas"), "No tileset yet")

    def _opts(self, slot: str, image: Path | None = None) -> TP.TileOptions:
        terrain = (self.base_terrain if slot == "base" else self.overlay_terrain).currentText()
        details = self.base_desc.text().strip()
        return TP.TileOptions(terrain=f"{terrain}, {details}" if details else terrain, seed=self.seed.seed(), import_image=image,
                              seamless=self.seamless.isChecked())

    def gen_tile(self, slot: str) -> None:
        asset = self.require_asset()
        if asset:
            opts = self._opts(slot)
            self.run_job(f"Tile {slot}: {asset.name}", lambda c: TP.make_tile(self.ctx(), asset, c, opts, slot))

    def import_tile(self, slot: str) -> None:
        asset = self.require_asset()
        path = self.pick_image(f"Import {slot} texture") if asset else None
        if path:
            opts = self._opts(slot, path)
            self.run_job(f"Import tile {slot}: {asset.name}", lambda c: TP.make_tile(self.ctx(), asset, c, opts, slot))

    def build_set(self, new_seed: bool = False) -> None:
        asset = self.require_asset()
        if asset:
            seed = None if new_seed else self.seed.seed()
            v, r = self.variants.value(), self.rough.value()
            self.run_job(f"Tileset: {asset.name}", lambda c: TP.build_tileset(self.ctx(), asset, seed=seed, variants=v, roughness=r))

    def run_full(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        base, over = self._opts("base"), self._opts("overlay")
        v, r, seed = self.variants.value(), self.rough.value(), self.seed.seed()
        is_set = asset.type == "tileset"

        def job(c):
            ctx = self.ctx()
            TP.make_tile(ctx, asset, c.sub(0, 0.45), base, "base")
            if is_set:
                TP.make_tile(ctx, asset, c.sub(0.45, 0.9), over, "overlay")
                TP.build_tileset(ctx, asset, seed=seed, variants=v, roughness=r)

        self.run_job(f"{'Tileset' if is_set else 'Tile'}: {asset.name}", job)


# ===================================================================== backgrounds
class BackgroundStudioPage(AssetStudioPage):
    type_keys = ("background",)
    title = "Background Studio"
    subtitle = "2D pipeline: concept/reference → style matching → pixel conversion. Optional parallax layers (sky, far, mid, near). Manual import supported."

    def extra_tabs(self) -> None:
        self.layer_view = PixelImageView()
        w = QWidget()
        lay = QVBoxLayout(w)
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._show_layer)
        lay.addWidget(self.layer_combo)
        lay.addWidget(self.layer_view, 1)
        self.tabs.insertTab(0, w, "Layers")

    def build_form(self, layout: QVBoxLayout) -> None:
        f = self.group("Background", layout)
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.description.setPlaceholderText("e.g. misty forest at dusk with distant castle")
        f.addRow("Description", self.description)
        size = QHBoxLayout()
        self.bw = spin(480, 32, 4096, " px")
        self.bh = spin(270, 32, 4096, " px")
        size.addWidget(self.bw)
        size.addWidget(QLabel("×"))
        size.addWidget(self.bh)
        f.addRow("Pixel resolution", size)
        self.layered = QCheckBox("Generate parallax layers (sky / far / mid / near)")
        f.addRow(self.layered)
        self.ref = line(placeholder="optional reference image")
        rr = QHBoxLayout()
        rr.addWidget(self.ref)
        rr.addWidget(button("…", lambda: self.ref.setText(str(self.pick_image() or ""))))
        f.addRow("Reference", rr)
        g = self.group("Generation", layout)
        self.seed = SeedSpin()
        g.addRow("Seed", self.seed)
        p = self.group("Pipeline", layout)
        p.addRow(button("▶ Generate Background", self.run_full, primary=True))
        row = QHBoxLayout()
        row.addWidget(button("Import Image…", self.import_bg))
        row.addWidget(button("Regenerate Layer", self.regen_layer))
        p.addRow(row)

    def load_form(self, asset: Asset | None) -> None:
        if asset is None:
            return
        self.description.setPlainText(asset.meta.description)
        self.bw.setValue(int(asset.meta.settings.get("width", 480)))
        self.bh.setValue(int(asset.meta.settings.get("height", 270)))
        self.layered.setChecked(bool(asset.meta.settings.get("layered", False)))

    def update_extra_views(self, asset: Asset) -> None:
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for l in BG.LAYERS:
            if asset.meta.outputs.get(LAYER_PREFIX + l):
                self.layer_combo.addItem(l)
        self.layer_combo.blockSignals(False)
        self._show_layer()

    def _show_layer(self, *_a) -> None:
        if self.asset and self.layer_combo.currentText():
            self.layer_view.set_image(self.asset.output_path(LAYER_PREFIX + self.layer_combo.currentText()))
        else:
            self.layer_view.set_image(None, "No layers (enable parallax layers)")

    def _opts(self, image=None) -> BG.BackgroundOptions:
        ref = Path(self.ref.text()) if self.ref.text().strip() and Path(self.ref.text()).is_file() else None
        return BG.BackgroundOptions(self.bw.value(), self.bh.value(), self.layered.isChecked(), seed=self.seed.seed(),
                                    reference_image=ref, import_image=image)

    def _save(self) -> Asset | None:
        asset = self.require_asset()
        if asset:
            asset.meta.description = self.description.toPlainText().strip()
            asset.save()
        return asset

    def run_full(self) -> None:
        asset = self._save()
        if asset:
            opts = self._opts()
            self.run_job(f"Background: {asset.name}", lambda c: BG.run_background(self.ctx(), asset, c, opts))

    def import_bg(self) -> None:
        asset = self._save()
        path = self.pick_image() if asset else None
        if path:
            opts = self._opts(path)
            self.run_job(f"Import background: {asset.name}", lambda c: BG.run_background(self.ctx(), asset, c, opts))

    def regen_layer(self) -> None:
        asset = self._save()
        layer = self.layer_combo.currentText()
        if asset and layer:
            w, h = self.bw.value(), self.bh.value()

            def job(c):
                BG.generate_layer(self.ctx(), asset, c, layer, w, h, None)
                BG.composite_layers(asset)

            self.run_job(f"Layer {layer}: {asset.name}", job)
        elif asset:
            self.show_error(StudioError("Select a layer in the Layers tab first."))


# ============================================================================= VFX
class VFXStudioPage(AssetStudioPage):
    type_keys = ("vfx",)
    title = "VFX Studio"
    subtitle = ("Frame-based effects generated deterministically (seeded particle simulation rendered at pixel resolution) – "
                "fire, smoke, explosions, magic, lightning, soul energy, healing, hits and impacts. Frames can also be imported.")

    def extra_tabs(self) -> None:
        self.preview = AnimationPreview()
        self.tabs.insertTab(0, self.preview, "Animation")
        self.tabs.setCurrentIndex(0)

    def build_form(self, layout: QVBoxLayout) -> None:
        f = self.group("Effect", layout)
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.frames = spin(8, 1, 64)
        size = QHBoxLayout()
        self.w = spin(32, 8, 512, " px")
        self.h = spin(32, 8, 512, " px")
        size.addWidget(self.w)
        size.addWidget(QLabel("×"))
        size.addWidget(self.h)
        self.loop = QCheckBox("Looping")
        self.loop.setChecked(True)
        self.fps = spin(12, 1, 60, " fps")
        self.direction = QComboBox()
        self.direction.addItems(list(VFX_DIRECTIONS))
        self.intensity = QDoubleSpinBox()
        self.intensity.setRange(0.2, 4.0)
        self.intensity.setValue(1.0)
        self.intensity.setSingleStep(0.1)
        self.snap = QCheckBox("Snap colours to project palette")
        self.seed = SeedSpin()
        for label, wdg in (("Preset", self.preset), ("Frame count", self.frames), ("Canvas size", size), ("", self.loop),
                           ("Timing", self.fps), ("Direction", self.direction), ("Intensity", self.intensity), ("", self.snap), ("Seed", self.seed)):
            if isinstance(wdg, QHBoxLayout):
                f.addRow(label, wdg)
            else:
                f.addRow(label, wdg)
        p = self.group("Pipeline", layout)
        p.addRow(button("▶ Generate Effect", self.generate, primary=True))
        row = QHBoxLayout()
        row.addWidget(button("Regenerate (new seed)", lambda: self.generate(new_seed=True)))
        row.addWidget(button("Import Frames…", self.import_frames))
        p.addRow(row)
        p.addRow(button("Save GIF Preview", self.save_gif))

    def load_form(self, asset: Asset | None) -> None:
        if asset is None:
            self.preview.set_frames([])
            return
        last = asset.last_generation("vfx")
        if last:
            p = last.params
            self.preset.setCurrentText(p.get("preset", "fire"))
            self.frames.setValue(int(p.get("frames", 8)))
            self.w.setValue(int(p.get("width", 32)))
            self.h.setValue(int(p.get("height", 32)))
            self.loop.setChecked(bool(p.get("loop", True)))
            self.fps.setValue(int(p.get("fps", 12)))
            self.direction.setCurrentText(p.get("direction", "up"))
            self.intensity.setValue(float(p.get("intensity", 1.0)))
        info = asset.meta.animations.get("effect")
        self.preview.set_frames([asset.root / r for r in (info.final_frames.get("s", []) if info else [])],
                                fps=info.fps if info else None, loop=info.loop if info else None)

    def generate(self, new_seed: bool = False) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        from pixel_rpg_studio.pipeline.common import resolve_seed

        seed = resolve_seed(None if new_seed else self.seed.seed())
        params = VFXParams(self.preset.currentText(), self.frames.value(), self.w.value(), self.h.value(), self.loop.isChecked(),
                           self.fps.value(), self.direction.currentText(), seed, self.intensity.value())
        snap = self.snap.isChecked()
        self.run_job(f"VFX: {asset.name}", lambda c: VX.run_vfx(self.ctx(), asset, params, snap))

    def import_frames(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Import frames (several PNGs, or one sheet)", "", "Images (*.png *.gif *.webp)")
        if not files:
            return
        sheet = (self.w.value(), self.h.value()) if len(files) == 1 else None
        fps, loop = self.fps.value(), self.loop.isChecked()
        self.run_job(f"Import VFX frames: {asset.name}", lambda c: VX.import_vfx_frames(self.ctx(), asset, [Path(f) for f in files], fps, loop, sheet))

    def save_gif(self) -> None:
        asset = self.require_asset()
        if asset and self.preview.paths:
            out = asset.path("exports", f"{asset.id}_preview.gif")
            out.parent.mkdir(parents=True, exist_ok=True)
            animated_preview([Image.open(p) for p in self.preview.paths], out, self.fps.value(), loop=self.loop.isChecked())
            self.services.message.emit(f"Saved {out}")


# ===================================================================== sprite sheets
class SpriteSheetPage(AssetStudioPage):
    type_keys = ("spritesheet",)
    title = "Sprite Sheet Builder"
    subtitle = "Arrange frames, define columns/rows/frame size/padding, preview the animation with FPS and loop, export PNG + metadata (+ Godot SpriteFrames)."

    def extra_tabs(self) -> None:
        self.preview = AnimationPreview()
        self.sheet_view = PixelImageView()
        self.tabs.insertTab(0, self.preview, "Animation")
        self.tabs.insertTab(1, self.sheet_view, "Sheet")
        self.tabs.setCurrentIndex(0)

    def build_form(self, layout: QVBoxLayout) -> None:
        f = self.group("Frames", layout)
        self.anim_name = line("idle")
        self.files = QListWidget()
        self.files.setFixedHeight(150)
        row = QHBoxLayout()
        row.addWidget(button("Add Frames…", self.add_files))
        row.addWidget(button("Up", lambda: self._move(-1)))
        row.addWidget(button("Down", lambda: self._move(1)))
        row.addWidget(button("Remove", lambda: self.files.takeItem(self.files.currentRow())))
        row2 = QHBoxLayout()
        row2.addWidget(button("Slice Existing Sheet…", self.slice_sheet))
        row2.addWidget(button("Frames from Asset…", self.from_asset))
        f.addRow("Animation name", self.anim_name)
        f.addRow(self.files)
        f.addRow(row)
        f.addRow(row2)
        l = self.group("Layout", layout)
        size = QHBoxLayout()
        self.fw = spin(32, 1, 2048, " px")
        self.fh = spin(32, 1, 2048, " px")
        size.addWidget(self.fw)
        size.addWidget(QLabel("×"))
        size.addWidget(self.fh)
        self.columns = spin(0, 0, 256)
        self.columns.setSpecialValueText("auto")
        self.padding = spin(0, 0, 64, " px")
        self.spacing = spin(0, 0, 64, " px")
        self.fps = spin(10, 1, 60, " fps")
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        self.fit = QComboBox()
        self.fit.addItems(["pad", "scale"])
        for label, w in (("Frame size", size), ("Columns", self.columns), ("Padding", self.padding), ("Spacing", self.spacing),
                         ("FPS", self.fps), ("", self.loop), ("Different sizes", self.fit)):
            l.addRow(label, w)
        p = self.group("Build", layout)
        p.addRow(button("▶ Build Sheet", self.build, primary=True))
        p.addRow(button("Preview Frames", self.preview_files))

    def update_extra_views(self, asset: Asset) -> None:
        self.sheet_view.set_image(asset.output_path("sprite_sheet"), "No sheet yet")
        info = next(iter(asset.meta.animations.values()), None)
        self.preview.set_frames([asset.root / r for r in (info.final_frames.get("s", []) if info else [])],
                                fps=info.fps if info else None, loop=info.loop if info else None)

    def _paths(self) -> list[Path]:
        return [Path(self.files.item(i).text()) for i in range(self.files.count())]

    def _move(self, delta: int) -> None:
        r = self.files.currentRow()
        if r < 0 or not (0 <= r + delta < self.files.count()):
            return
        item = self.files.takeItem(r)
        self.files.insertItem(r + delta, item)
        self.files.setCurrentRow(r + delta)

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add frames", "", "Images (*.png *.webp *.bmp)")
        for f in sorted(files):
            self.files.addItem(f)
        if files:
            img = Image.open(files[0])
            self.fw.setValue(img.width)
            self.fh.setValue(img.height)

    def slice_sheet(self) -> None:
        asset = self.require_asset()
        path = self.pick_image("Sprite sheet to slice") if asset else None
        if not path:
            return
        from pixel_rpg_studio.spritesheet.builder import slice_sheet

        frames = slice_sheet(Image.open(path), self.fw.value(), self.fh.value())
        out = asset.path("imported_frames")
        out.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            p = out / f"{path.stem}_{i:03d}.png"
            f.save(p)
            self.files.addItem(str(p))
        self.services.message.emit(f"Sliced {len(frames)} frames from {path.name}")

    def from_asset(self) -> None:
        project = self.services.project
        if project is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Select a frames folder (e.g. <asset>/animations/walk/s/final)", str(project.root))
        if folder:
            for p in sorted(Path(folder).glob("*.png")):
                self.files.addItem(str(p))

    def preview_files(self) -> None:
        self.preview.set_frames(self._paths(), fps=self.fps.value(), loop=self.loop.isChecked())
        self.tabs.setCurrentIndex(0)

    def build(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        files = self._paths()
        if not files:
            self.show_error(StudioError("Add frames first."))
            return
        name = self.anim_name.text().strip() or "anim"
        args = dict(frame_size=(self.fw.value(), self.fh.value()), fps=self.fps.value(), loop=self.loop.isChecked(),
                    columns=self.columns.value(), padding=self.padding.value(), spacing=self.spacing.value(), fit=self.fit.currentText())
        self.run_job(f"Sprite sheet: {asset.name}", lambda c: SH.build_from_files(asset, {name: files}, **args))
