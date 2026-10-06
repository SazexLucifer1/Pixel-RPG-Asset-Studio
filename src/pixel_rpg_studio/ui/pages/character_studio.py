"""Character Studio: concept → 3D → rig → animate → render → pixel → sheet → Godot."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.imaging.compare import compare_to_master
from pixel_rpg_studio.pipeline import three_d
from pixel_rpg_studio.pipeline.character import CharacterRunOptions, run_character
from pixel_rpg_studio.pipeline.common import build_asset_sheet, build_prompt, generate_concept, import_image
from pixel_rpg_studio.project.asset import ROLE_MASTER, Asset
from pixel_rpg_studio.project.asset_types import ANIMATION_DEFAULTS, ANIMATIONS, DIRECTION_SETS, DIRECTIONS
from pixel_rpg_studio.ui.pages.studio_base import AssetStudioPage, SeedSpin, line, spin
from pixel_rpg_studio.ui.widgets.animation_preview import AnimationPreview
from pixel_rpg_studio.ui.widgets.common import button
from pixel_rpg_studio.ui.widgets.dialogs import open_path

FACING = {"As generated (0°)": 0.0, "Rotate 90°": 90.0, "Rotate 180° (model faces away)": 180.0, "Rotate 270°": 270.0}
FRAMING = {"Same scale for all animations (recommended)": "all_animations", "Fit selected animations only": "selected_animations"}


class CharacterStudioPage(AssetStudioPage):
    type_keys = ("character",)
    title = "Character Studio"
    subtitle = ("Concept → reference → 3D model → cleanup → materials → automatic rig → animation → render → pixel art → "
                "sprite sheet → Godot. Every stage can be re-run on its own; results are kept with full metadata.")

    # ---------------------------------------------------------------- tabs
    def extra_tabs(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.anim_combo = QComboBox()
        self.dir_combo = QComboBox()
        self.raw_toggle = QCheckBox("Show raw render")
        for wdg in (QLabel("Animation"), self.anim_combo, QLabel("Direction"), self.dir_combo, self.raw_toggle):
            top.addWidget(wdg)
        top.addStretch(1)
        self.anim_combo.currentIndexChanged.connect(self._fill_dirs)
        self.dir_combo.currentIndexChanged.connect(self._show_animation)
        self.raw_toggle.toggled.connect(self._show_animation)
        self.preview = AnimationPreview()
        self.preview.frame_selected.connect(self._frame_selected)
        self.frame_info = QLabel("")
        self.frame_info.setObjectName("Muted")
        actions = QHBoxLayout()
        actions.addWidget(button("Regenerate Selected Frame", self.regenerate_frame,
                                 tooltip="Re-render only this frame with the stored camera, model and settings"))
        actions.addWidget(button("Re-render This Animation", self.rerender_animation))
        actions.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.frame_info)
        lay.addLayout(actions)
        self.tabs.insertTab(0, w, "Animation")
        self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------------- form
    def build_form(self, layout: QVBoxLayout) -> None:
        f = self.group("Character", layout)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("e.g. veteran soldier, short brown hair, steel breastplate, red cape")
        self.description.setFixedHeight(70)
        self.body_type = QComboBox()
        self.body_type.addItems(["humanoid", "creature"])
        self.equipment = line(placeholder="armor, clothing, accessories")
        self.weapon = line(placeholder="e.g. longsword in right hand")
        self.colors = line(placeholder="e.g. blue and silver, red cape")
        f.addRow("Description", self.description)
        f.addRow("Body type", self.body_type)
        f.addRow("Equipment", self.equipment)
        f.addRow("Weapon", self.weapon)
        f.addRow("Color palette", self.colors)

        r = self.group("Reference images", layout)
        self.refs = QListWidget()
        self.refs.setFixedHeight(70)
        refrow = QHBoxLayout()
        refrow.addWidget(button("Add…", self.add_reference))
        refrow.addWidget(button("Remove", self.remove_reference))
        r.addRow(self.refs)
        r.addRow(refrow)
        self.use_ref = QCheckBox("Use first reference to guide the concept (img2img)")
        r.addRow(self.use_ref)

        o = self.group("Output", layout)
        size_row = QHBoxLayout()
        self.sprite_w = spin(48, 8, 512, " px")
        self.sprite_h = spin(48, 8, 512, " px")
        size_row.addWidget(self.sprite_w)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.sprite_h)
        o.addRow("Target resolution", size_row)
        self.dir_set = QComboBox()
        self.dir_set.addItems(list(DIRECTION_SETS))
        o.addRow("Camera directions", self.dir_set)
        self.facing = QComboBox()
        self.facing.addItems(list(FACING))
        self.facing.setToolTip("If the generated model faces away from the camera, rotate it here and prepare again.")
        o.addRow("Model facing", self.facing)
        self.framing = QComboBox()
        self.framing.addItems(list(FRAMING))
        o.addRow("Framing", self.framing)

        a = self.group("Animations", layout)
        self.anim_table = QTableWidget(len(ANIMATIONS), 4)
        self.anim_table.setHorizontalHeaderLabels(["Animation", "Frames", "FPS", "Loop"])
        self.anim_table.verticalHeader().setVisible(False)
        self.anim_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for row, name in enumerate(ANIMATIONS):
            item = QTableWidgetItem(name.replace("_", " ").title())
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if name in ("idle", "walk") else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.anim_table.setItem(row, 0, item)
            d = ANIMATION_DEFAULTS[name]
            self.anim_table.setCellWidget(row, 1, spin(d["frames"], 1, 64))
            self.anim_table.setCellWidget(row, 2, spin(d["fps"], 1, 60))
            loop = QCheckBox()
            loop.setChecked(d["loop"])
            self.anim_table.setCellWidget(row, 3, loop)
        self.anim_table.setFixedHeight(300)
        a.addRow(self.anim_table)
        note = QLabel("Automatic rigging is heuristic (upright T/A-pose humanoids). Check the rig report after "
                      "'Prepare model'; you can fix the rig in Blender ('Open .blend') and re-render.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        a.addRow(note)

        g = self.group("Generation", layout)
        self.seed = SeedSpin()
        self.model_seed = SeedSpin()
        g.addRow("Concept seed", self.seed)
        g.addRow("3D seed", self.model_seed)

        p = self.group("Pipeline", layout)
        p.addRow(button("▶ Run Full Pipeline", self.run_full, primary=True,
                        tooltip="Concept → 3D → prepare → render all selected animations → sprite sheet"))
        steps = [
            ("1. Generate Concept", self.gen_concept), ("Import Concept Image…", self.import_concept),
            ("2. Generate 3D Model", self.gen_model), ("Import 3D Model…", self.import_model),
            ("3. Prepare Model (Blender)", self.prepare_model), ("4. Render Animations", self.render),
            ("5. Build Sprite Sheet", self.build_sheet), ("Open .blend in Blender", self.open_blend),
        ]
        for i in range(0, len(steps), 2):
            row = QHBoxLayout()
            for text, slot in steps[i : i + 2]:
                row.addWidget(button(text, slot))
            p.addRow(row)
        self.rig_report = QLabel("")
        self.rig_report.setWordWrap(True)
        self.rig_report.setObjectName("Muted")
        p.addRow(self.rig_report)

    # ------------------------------------------------------------ form io
    def load_form(self, asset: Asset | None) -> None:
        style = self.services.project.style if self.services.project else None
        if asset is None:
            self.refs.clear()
            self.rig_report.setText("")
            self.anim_combo.clear()
            self.dir_combo.clear()
            self.preview.set_frames([])
            return
        s = three_d.settings(asset, style) if style else asset.meta.settings
        self.description.setPlainText(asset.meta.description)
        self.body_type.setCurrentText(asset.meta.subtype or "humanoid")
        self.equipment.setText(s.get("equipment", ""))
        self.weapon.setText(s.get("weapon", ""))
        self.colors.setText(s.get("colors", ""))
        self.sprite_w.setValue(int(s.get("sprite_width", 48)))
        self.sprite_h.setValue(int(s.get("sprite_height", 48)))
        dirs = tuple(s.get("directions", ()))
        for name, keys in DIRECTION_SETS.items():
            if keys == dirs:
                self.dir_set.setCurrentText(name)
        for label, deg in FACING.items():
            if float(s.get("facing_correction_deg", 0)) == deg:
                self.facing.setCurrentText(label)
        for label, key in FRAMING.items():
            if s.get("framing") == key:
                self.framing.setCurrentText(label)
        anims = s.get("animations") or {}
        for row, name in enumerate(ANIMATIONS):
            cfg = anims.get(name)
            self.anim_table.item(row, 0).setCheckState(Qt.CheckState.Checked if cfg else Qt.CheckState.Unchecked)
            cfg = cfg or ANIMATION_DEFAULTS[name]
            self.anim_table.cellWidget(row, 1).setValue(int(cfg["frames"]))
            self.anim_table.cellWidget(row, 2).setValue(int(cfg["fps"]))
            self.anim_table.cellWidget(row, 3).setChecked(bool(cfg["loop"]))
        self.refs.clear()
        for p in sorted(asset.path("references").glob("*")) if asset.path("references").exists() else []:
            self.refs.addItem(p.name)
        rep = s.get("prepare_report") or {}
        rig = rep.get("rig") or {}
        text = ""
        if rig:
            text = f"Rig: {rig.get('mode')} · weights: {rig.get('weights', '-')} · unweighted vertices: {rig.get('unweighted_vertices', '-')}"
        if rep.get("warnings"):
            text += "\nWarnings: " + "; ".join(rep["warnings"])
        self.rig_report.setText(text)
        # animation preview
        self.anim_combo.blockSignals(True)
        self.anim_combo.clear()
        for name in asset.meta.animations:
            self.anim_combo.addItem(name)
        self.anim_combo.blockSignals(False)
        self._fill_dirs()

    def save_form(self) -> Asset | None:
        asset = self.require_asset()
        if asset is None:
            return None
        style = self.services.require_project().style
        s = three_d.settings(asset, style)
        asset.meta.description = self.description.toPlainText().strip()
        asset.meta.subtype = self.body_type.currentText()
        s.update({
            "equipment": self.equipment.text().strip(), "weapon": self.weapon.text().strip(), "colors": self.colors.text().strip(),
            "sprite_width": self.sprite_w.value(), "sprite_height": self.sprite_h.value(),
            "directions": list(DIRECTION_SETS[self.dir_set.currentText()]),
            "framing": FRAMING[self.framing.currentText()],
        })
        new_facing = FACING[self.facing.currentText()]
        if float(s.get("facing_correction_deg", 0.0)) != new_facing:
            s["facing_correction_deg"] = new_facing
            s.pop("camera_framing", None)
        anims = {}
        for row, name in enumerate(ANIMATIONS):
            if self.anim_table.item(row, 0).checkState() == Qt.CheckState.Checked:
                anims[name] = {"frames": self.anim_table.cellWidget(row, 1).value(), "fps": self.anim_table.cellWidget(row, 2).value(),
                               "loop": self.anim_table.cellWidget(row, 3).isChecked()}
        s["animations"] = anims
        for name, info in asset.meta.animations.items():
            if name in anims:
                info.fps = anims[name]["fps"]
                info.loop = anims[name]["loop"]
        asset.save()
        return asset

    def selected_animations(self) -> list[str]:
        return [ANIMATIONS[r] for r in range(len(ANIMATIONS)) if self.anim_table.item(r, 0).checkState() == Qt.CheckState.Checked]

    def extra_prompt(self) -> str:
        parts = [self.equipment.text().strip(), self.weapon.text().strip(), self.colors.text().strip()]
        if self.body_type.currentText() == "creature":
            parts.append("creature")
        return ", ".join(p for p in parts if p)

    def reference(self) -> Path | None:
        if not self.use_ref.isChecked() or self.asset is None or self.refs.count() == 0:
            return None
        return self.asset.path("references", self.refs.item(0).text())

    # --------------------------------------------------------------- actions
    def add_reference(self) -> None:
        asset = self.require_asset()
        path = self.pick_image("Add reference image") if asset else None
        if path:
            dest = asset.path("references", path.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.open(path).convert("RGBA").save(dest.with_suffix(".png"))
            self.load_form(asset)

    def remove_reference(self) -> None:
        item = self.refs.currentItem()
        if item and self.asset:
            self.asset.path("references", item.text()).unlink(missing_ok=True)
            self.load_form(self.asset)

    def gen_concept(self) -> None:
        asset = self.save_form()
        if asset is None:
            return
        project = self.services.project
        prompt, negative = build_prompt(project, "character", asset.meta.description, asset.meta.subtype, self.extra_prompt())
        seed, ref = self.seed.seed(), self.reference()

        def job(c):
            ctx = self.ctx()
            generate_concept(ctx, asset, c, prompt=prompt, negative=negative, seed=seed, reference=ref)
            three_d.make_master_reference(ctx, asset)

        self.run_job(f"Concept: {asset.name}", job)

    def import_concept(self) -> None:
        asset = self.save_form()
        path = self.pick_image("Import concept image (full body, front view, plain background)") if asset else None
        if path:
            def job(c):
                import_image(asset, path)
                three_d.make_master_reference(self.ctx(), asset)

            self.run_job(f"Import concept: {asset.name}", job)

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

    def prepare_model(self) -> None:
        asset = self.save_form()
        if asset:
            self.run_job(f"Prepare model: {asset.name}", lambda c: three_d.prepare_model(self.ctx(), asset, c))

    def render(self) -> None:
        asset = self.save_form()
        if asset is None:
            return
        anims = self.selected_animations()
        if not anims:
            self.show_error(StudioError("Select at least one animation."))
            return

        def job(c):
            three_d.render_frames(self.ctx(), asset, c, animations=anims)
            s = asset.meta.settings
            build_asset_sheet(asset, (int(s["sprite_width"]), int(s["sprite_height"])))

        self.run_job(f"Render animations: {asset.name}", job)

    def build_sheet(self) -> None:
        asset = self.save_form()
        if asset:
            s = asset.meta.settings
            self.run_job(f"Sprite sheet: {asset.name}", lambda c: build_asset_sheet(asset, (int(s["sprite_width"]), int(s["sprite_height"]))))

    def run_full(self) -> None:
        asset = self.save_form()
        if asset is None:
            return
        opts = CharacterRunOptions(seed=self.seed.seed(), model_seed=self.model_seed.seed(), reference_image=self.reference(),
                                   animations=self.selected_animations() or ["idle"], extra_prompt=self.extra_prompt(),
                                   reuse_concept=False, reuse_model=False)
        self.run_job(f"Full character pipeline: {asset.name}", lambda c: run_character(self.ctx(), asset, c, opts))

    def open_blend(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        blend = asset.meta.settings.get("blend")
        if not blend:
            self.show_error(StudioError("The model has not been prepared yet.", hint="Run 'Prepare Model' first."))
            return
        try:
            from pixel_rpg_studio.blender.detect import find_blender

            info = find_blender(self.services.settings.blender.executable)
            if info is None:
                raise StudioError("Blender not found.", hint="Configure Blender in Settings.")
            import subprocess

            subprocess.Popen([str(info.executable), str(asset.root / blend)])
            self.services.message.emit("Blender opened. Save your changes there, then press '4. Render Animations'.")
        except StudioError as exc:
            self.show_error(exc)
        except OSError:
            open_path(asset.root / blend)

    # ------------------------------------------------------------- preview
    def _fill_dirs(self, *_args) -> None:
        self.dir_combo.blockSignals(True)
        self.dir_combo.clear()
        info = self.asset.meta.animations.get(self.anim_combo.currentText()) if self.asset else None
        for d in (info.directions if info else []):
            self.dir_combo.addItem(f"{d} – {DIRECTIONS[d].label}" if d in DIRECTIONS else d, d)
        self.dir_combo.blockSignals(False)
        self._show_animation()

    def _show_animation(self, *_args) -> None:
        asset = self.asset
        info = asset.meta.animations.get(self.anim_combo.currentText()) if asset else None
        d = self.dir_combo.currentData()
        if not info or not d:
            self.preview.set_frames([])
            return
        rels = (info.raw_frames if self.raw_toggle.isChecked() else info.final_frames).get(d, [])
        self.preview.set_frames([asset.root / r for r in rels], fps=info.fps, loop=info.loop)

    def _frame_selected(self, index: int) -> None:
        asset = self.asset
        if not asset:
            return
        key = f"{self.anim_combo.currentText()}/{self.dir_combo.currentData()}/{index}"
        text = asset.meta.settings.get("consistency", {}).get(key, "")
        self.frame_info.setText(f"Frame {index}: {text}" if text else f"Frame {index}")

    def regenerate_frame(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        anim, d, idx = self.anim_combo.currentText(), self.dir_combo.currentData(), self.preview.index
        if not anim or not d:
            self.show_error(StudioError("Select an animation frame first (Animation tab)."))
            return

        def job(c):
            three_d.regenerate_frame(self.ctx(), asset, c, anim, d, idx)
            s = asset.meta.settings
            build_asset_sheet(asset, (int(s["sprite_width"]), int(s["sprite_height"])))

        self.run_job(f"Regenerate frame {idx} of {anim}/{d}", job)

    def rerender_animation(self) -> None:
        asset = self.save_form()
        anim = self.anim_combo.currentText()
        if asset and anim:
            def job(c):
                three_d.render_frames(self.ctx(), asset, c, animations=[anim])
                s = asset.meta.settings
                build_asset_sheet(asset, (int(s["sprite_width"]), int(s["sprite_height"])))

            self.run_job(f"Re-render {anim}: {asset.name}", job)

    def compare_images(self):
        asset = self.asset
        master_p = asset.output_path(ROLE_MASTER)
        frame_p = self.preview.paths[self.preview.index] if self.preview.paths else None
        if master_p and frame_p:
            master, frame = Image.open(master_p).convert("RGBA"), Image.open(frame_p).convert("RGBA")
            summary = compare_to_master(master, frame).summary()
            return [master, frame], ["Master reference", f"{self.anim_combo.currentText()} frame {self.preview.index}"], summary
        return super().compare_images()
