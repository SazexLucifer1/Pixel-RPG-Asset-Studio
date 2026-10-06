"""Character Studio: reference image → identity → pose → AI frames → pixel art → sheets → Godot.

One workflow only. The reference image is the character's identity (sent
as real image conditioning to the AI for every frame); poses come from the
skeleton presets / pose editor as OpenPose images.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.imaging.compare import compare_to_master
from pixel_rpg_studio.pipeline import character as ch
from pixel_rpg_studio.poses import presets
from pixel_rpg_studio.project.asset import ROLE_MASTER, ROLE_ORIGINAL, Asset
from pixel_rpg_studio.ui.pages.studio_base import AssetStudioPage, SeedSpin
from pixel_rpg_studio.ui.widgets.animation_preview import AnimationPreview
from pixel_rpg_studio.ui.widgets.common import button
from pixel_rpg_studio.ui.widgets.dialogs import open_path
from pixel_rpg_studio.ui.widgets.image_view import PixelImageView, load_qimage
from pixel_rpg_studio.ui.widgets.pose_editor import PoseEditor

ALL_DIRECTIONS = "all"
CUSTOM = "__custom__"


class StrengthSlider(QWidget):
    """Slider 0..1.5 with a numeric box; ``value()`` is the float."""

    def __init__(self, value: float, tooltip: str) -> None:
        super().__init__()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 150)
        self.box = QDoubleSpinBox()
        self.box.setRange(0.0, 1.5)
        self.box.setSingleStep(0.05)
        self.box.setDecimals(2)
        self.slider.valueChanged.connect(lambda v: self.box.setValue(v / 100))
        self.box.valueChanged.connect(lambda v: self.slider.setValue(int(round(v * 100))))
        self.setToolTip(tooltip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.box)
        self.setValue(value)

    def value(self) -> float:
        return round(self.box.value(), 3)

    def setValue(self, v: float) -> None:
        self.box.setValue(float(v))


class CharacterStudioPage(AssetStudioPage):
    type_keys = ("character",)
    title = "Character Studio"
    subtitle = ("Reference image → character identity → pose → AI frame (reference conditioning + OpenPose) → pixel art → "
                "animation frames → sprite sheet → Godot. Frames are generated one at a time.")

    # ---------------------------------------------------------------- tabs
    def extra_tabs(self) -> None:
        # Animation preview
        anim = QWidget()
        al = QVBoxLayout(anim)
        self.preview = AnimationPreview()
        self.preview.frame_selected.connect(self._preview_frame)
        al.addWidget(self.preview, 1)
        self.tabs.insertTab(0, anim, "Animation")
        # Pose editor
        pose = QWidget()
        pl = QVBoxLayout(pose)
        self.pose_editor = PoseEditor()
        self.pose_editor.pose_edited.connect(self._pose_edited)
        self.pose_info = QLabel("Drag the joints (head, neck, shoulders, elbows, hands, hips, knees, feet). "
                                "Changes are saved for the selected frame.")
        self.pose_info.setObjectName("Muted")
        self.pose_info.setWordWrap(True)
        row = QHBoxLayout()
        self.underlay = QComboBox()
        self.underlay.addItems(["No underlay", "Generated frame", "Reference"])
        self.underlay.currentIndexChanged.connect(self._update_underlay)
        row.addWidget(QLabel("Underlay"))
        row.addWidget(self.underlay)
        row.addWidget(button("Mirror", self.mirror_pose, tooltip="Mirror the pose left/right"))
        row.addWidget(button("Reset to Template", self.reset_pose))
        row.addWidget(button("Save as Preset…", self.save_preset))
        row.addStretch(1)
        pl.addWidget(self.pose_editor, 1)
        pl.addLayout(row)
        pl.addWidget(self.pose_info)
        self.tabs.insertTab(1, pose, "Pose Editor")
        self.tabs.setCurrentIndex(0)

    # ---------------------------------------------------------------- form
    def build_form(self, layout: QVBoxLayout) -> None:
        # CHARACTER
        f = self.group("CHARACTER", layout)
        r = QHBoxLayout()
        r.addWidget(button("Reference Image", self.choose_reference, tooltip="The character to animate (full body, plain background works best)"))
        r.addWidget(button("Create Character", self.create_character, primary=True,
                           tooltip="Builds the identity: cleaned reference, pixel preview, palette locked from the reference"))
        f.addRow(r)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Optional words that support the reference, e.g. 'knight in silver plate armor, blue visor, sword and shield'")
        self.description.setFixedHeight(56)
        f.addRow("Description", self.description)
        self.size_combo = QComboBox()
        for s in ch.SPRITE_SIZES:
            self.size_combo.addItem(f"{s} × {s}", s)
        self.size_combo.setCurrentIndex(ch.SPRITE_SIZES.index(ch.DEFAULT_SPRITE_SIZE))
        f.addRow("Sprite size", self.size_combo)
        er = QHBoxLayout()
        er.addWidget(button("Equipment Reference…", self.choose_equipment,
                            tooltip="Optional close-up of weapon/shield; used as additional image conditioning"))
        er.addWidget(button("Remove", self.remove_equipment))
        self.equipment_label = QLabel("none")
        self.equipment_label.setObjectName("Muted")
        er.addWidget(self.equipment_label, 1)
        f.addRow("Equipment", er)

        # IDENTITY
        i = self.group("IDENTITY", layout)
        prev = QHBoxLayout()
        self.ref_view = PixelImageView(min_size=110)
        self.identity_view = PixelImageView(min_size=110)
        prev.addWidget(self.ref_view)
        prev.addWidget(self.identity_view)
        i.addRow(prev)
        self.identity_label = QLabel("No identity yet – choose a reference image and press 'Create Character'.")
        self.identity_label.setObjectName("Muted")
        self.identity_label.setWordWrap(True)
        i.addRow(self.identity_label)
        self.ref_strength = StrengthSlider(ch.DEFAULT_REFERENCE_STRENGTH,
                                           "How strongly the reference image controls the result (IP-Adapter weight)")
        self.pose_strength = StrengthSlider(ch.DEFAULT_POSE_STRENGTH, "How strictly the skeleton is followed (ControlNet strength)")
        i.addRow("Reference Strength", self.ref_strength)
        i.addRow("Pose Strength", self.pose_strength)

        # ANIMATION
        a = self.group("ANIMATION", layout)
        self.anim_combo = QComboBox()
        self.dir_combo = QComboBox()
        for d in ch.DIRECTIONS:
            self.dir_combo.addItem(ch.DIRECTION_LABELS[d], d)
        self.dir_combo.addItem("All (Front, Back, Left, Right)", ALL_DIRECTIONS)
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 32)
        self.anim_combo.currentIndexChanged.connect(self._anim_changed)
        self.dir_combo.currentIndexChanged.connect(self._refresh_frames)
        a.addRow("Animation", self.anim_combo)
        a.addRow("Direction", self.dir_combo)
        a.addRow("Frames", self.frames_spin)

        # POSE
        p = self.group("POSE", layout)
        pr = QHBoxLayout()
        pr.addWidget(button("Pose Editor", lambda: self.tabs.setCurrentIndex(1), tooltip="Edit the selected frame's skeleton"))
        self.preset_combo = QComboBox()
        pr.addWidget(self.preset_combo, 1)
        pr.addWidget(button("Preset Pose", self.apply_preset, tooltip="Apply the chosen preset to the selected frame"))
        p.addRow(pr)

        # GENERATE
        g = self.group("GENERATE", layout)
        self.seed = SeedSpin()
        self.seed.setToolTip("Seed for all frames of the animation. Random = the character's seed (stored in identity.json).")
        g.addRow("Seed", self.seed)
        g.addRow(button("Generate Animation", self.generate_animation, primary=True,
                        tooltip="Generates every frame of the selected animation and direction(s), one frame at a time"))

        # FRAMES
        fr = self.group("FRAMES", layout)
        self.frame_list = QListWidget()
        self.frame_list.setViewMode(QListView.ViewMode.IconMode)
        self.frame_list.setFlow(QListView.Flow.LeftToRight)
        self.frame_list.setWrapping(True)
        self.frame_list.setIconSize(QSize(48, 48))
        self.frame_list.setGridSize(QSize(62, 70))
        self.frame_list.setFixedHeight(150)
        self.frame_list.setMovement(QListView.Movement.Static)
        self.frame_list.currentRowChanged.connect(self._frame_selected)
        fr.addRow(self.frame_list)
        self.frame_label = QLabel("")
        self.frame_label.setObjectName("Muted")
        self.frame_label.setWordWrap(True)
        fr.addRow(self.frame_label)
        fr.addRow(button("Regenerate Selected Frame", self.regenerate_frame,
                         tooltip="Only this frame: same identity, reference, pose, animation, direction and style - new seed"))

        # EXPORT
        e = self.group("EXPORT", layout)
        er = QHBoxLayout()
        er.addWidget(button("Export PNG", self.export_png, tooltip="All finished frames as single PNG files"))
        er.addWidget(button("Export Sprite Sheet", self.export_sheet, tooltip="<name>_<animation>.png + .json (one row per direction)"))
        er.addWidget(button("Export to Godot", self.export_asset, primary=True))
        e.addRow(er)
        self._fill_animations(None)
        self._fill_presets()

    # ------------------------------------------------------------ helpers
    def _identity(self) -> dict | None:
        return ch.load_identity(self.asset) if self.asset else None

    def current_animation(self) -> str:
        return self.anim_combo.currentData() or ""

    def current_direction(self) -> str:
        d = self.dir_combo.currentData()
        return ch.DIRECTIONS[0] if d in (None, ALL_DIRECTIONS) else d

    def selected_directions(self) -> list[str]:
        d = self.dir_combo.currentData()
        return list(ch.DIRECTIONS) if d == ALL_DIRECTIONS else [d or ch.DIRECTIONS[0]]

    def current_frame(self) -> int:
        return max(0, self.frame_list.currentRow())

    def user_preset_dir(self) -> Path | None:
        project = self.services.project
        return project.root / "poses" / "presets" if project else None

    def _fill_presets(self) -> None:
        self.preset_combo.clear()
        for name in presets.BUILTIN_PRESETS:
            self.preset_combo.addItem(name)
        for name in presets.list_user_presets(self.user_preset_dir()):
            self.preset_combo.addItem(f"{name} (saved)", name)

    def _fill_animations(self, asset: Asset | None) -> None:
        current = self.current_animation()
        self.anim_combo.blockSignals(True)
        self.anim_combo.clear()
        for name in presets.STANDARD_ANIMATIONS:
            done = asset is not None and name in asset.meta.animations
            self.anim_combo.addItem(name.title() + ("  ✓" if done else ""), name)
        if asset is not None:
            for name in asset.meta.animations:
                if name not in presets.STANDARD_ANIMATIONS:
                    self.anim_combo.addItem(f"{name} (custom)", name)
        self.anim_combo.addItem("Custom…", CUSTOM)
        idx = self.anim_combo.findData(current or "idle")
        self.anim_combo.setCurrentIndex(max(0, idx))
        self.anim_combo.blockSignals(False)
        self._anim_changed()

    # ------------------------------------------------------------ form io
    def load_form(self, asset: Asset | None) -> None:
        self._fill_presets()
        self.ref_view.set_image(asset.output_path(ROLE_ORIGINAL) if asset else None, "No reference")
        identity = ch.load_identity(asset) if asset else None
        if identity:
            self.identity_view.set_image(asset.root / identity["pixel_preview"], "Identity")
            self.description.setPlainText(identity.get("description", ""))
            self.ref_strength.setValue(identity.get("reference_strength", ch.DEFAULT_REFERENCE_STRENGTH))
            self.pose_strength.setValue(identity.get("pose_strength", ch.DEFAULT_POSE_STRENGTH))
            self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(int(identity.get("sprite_size", ch.DEFAULT_SPRITE_SIZE)))))
            self.identity_label.setText(f"Identity: {len(identity['palette'])} colours locked from the reference · "
                                        f"{identity['sprite_size']}×{identity['sprite_size']} px · AI canvas {identity['generation_size']} px · "
                                        f"seed {identity['seed']}")
            eq = identity.get("equipment_reference")
            self.equipment_label.setText(Path(eq).name if eq else "none")
        else:
            self.identity_view.set_image(None, "Not created")
            self.identity_label.setText("No identity yet – choose a reference image and press 'Create Character'."
                                        if asset else "Select or create a character.")
            self.equipment_label.setText("none")
            if asset is not None:
                self.description.setPlainText(asset.meta.description)
        self._fill_animations(asset)

    def _anim_changed(self, *_args) -> None:
        name = self.current_animation()
        if name == CUSTOM:
            self.anim_combo.blockSignals(True)
            self.anim_combo.setCurrentIndex(0)
            self.anim_combo.blockSignals(False)
            self.add_custom_animation()
            return
        info = self.asset.meta.animations.get(name) if self.asset else None
        self.frames_spin.setValue(info.frames if info else presets.template(name).frames)
        self._refresh_frames()

    def _refresh_frames(self, *_args) -> None:
        asset, name, d = self.asset, self.current_animation(), self.current_direction()
        row = self.frame_list.currentRow()
        self.frame_list.blockSignals(True)
        self.frame_list.clear()
        info = asset.meta.animations.get(name) if asset else None
        finals: list[Path] = []
        if info is not None and d in info.directions:
            rels = info.final_frames.get(d) or []
            for i in range(info.frames):
                rel = rels[i] if i < len(rels) else ""
                path = asset.root / rel if rel else asset.path("animations", name, d, "pose", f"frame_{i:03d}.png")
                item = QListWidgetItem(f"{i + 1:02d}")
                q = load_qimage(path) if path.exists() else None
                if q is not None:
                    item.setIcon(QIcon(QPixmap.fromImage(q.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio))))
                self.frame_list.addItem(item)
                if rel:
                    finals.append(asset.root / rel)
        self.frame_list.blockSignals(False)
        if self.frame_list.count():
            self.frame_list.setCurrentRow(min(max(0, row), self.frame_list.count() - 1))
        else:
            self.frame_label.setText("No frames yet. Choose animation + direction and press 'Generate Animation'."
                                     if asset else "")
            self.pose_editor.set_pose(None)
        self.preview.set_frames(finals, fps=info.fps if info else None, loop=info.loop if info else None)

    def _frame_selected(self, row: int) -> None:
        asset = self.asset
        if asset is None or row < 0:
            return
        name, d = self.current_animation(), self.current_direction()
        try:
            pose = ch.get_pose(asset, name, d, row)
        except StudioError:
            pose = None
        self.pose_editor.set_pose(pose)
        self._update_underlay()
        seed = ch.frame_seed(asset, name, d, row)
        last = asset.last_generation("frame", f"{name}/{d}/{row}")
        text = f"Frame {row + 1:02d} · {name} · {ch.DIRECTION_LABELS.get(d, d)}"
        text += f" · seed {seed}" if seed is not None else " · not generated yet"
        if pose is not None:
            text += f" · pose: {pose.source or 'template'}"
        if last is not None:
            text += f" · reference {last.params.get('reference_strength')} / pose {last.params.get('pose_strength')}"
        self.frame_label.setText(text)

    def _preview_frame(self, index: int) -> None:
        if 0 <= index < self.frame_list.count() and self.frame_list.currentRow() != index:
            self.frame_list.blockSignals(True)
            self.frame_list.setCurrentRow(index)
            self.frame_list.blockSignals(False)

    def _update_underlay(self, *_args) -> None:
        asset = self.asset
        path = None
        if asset is not None:
            if self.underlay.currentIndex() == 1:
                path = asset.path("animations", self.current_animation(), self.current_direction(), "raw", f"frame_{self.current_frame():03d}.png")
            elif self.underlay.currentIndex() == 2:
                ident = self._identity()
                path = asset.root / ident["reference_clean"] if ident else asset.output_path(ROLE_ORIGINAL)
        self.pose_editor.set_underlay(path)

    def _save_identity_settings(self) -> dict | None:
        """Write the slider/size/description values into identity.json."""
        asset = self.require_asset()
        if asset is None:
            return None
        identity = self._identity()
        if identity is None:
            self.show_error(StudioError("This character has no identity yet.",
                                        hint="Choose a reference image and press 'Create Character' first."))
            return None
        new_size = int(self.size_combo.currentData())
        size_changed = new_size != int(identity["sprite_size"])
        identity = ch.update_identity(asset, reference_strength=self.ref_strength.value(), pose_strength=self.pose_strength.value(),
                                      sprite_size=new_size, description=self.description.toPlainText().strip())
        if size_changed:
            self.run_job(f"Re-process frames at {new_size} px: {asset.name}", lambda c: ch.reprocess_frames(self.ctx(), asset, c))
        return identity

    # --------------------------------------------------------------- actions
    def choose_reference(self) -> None:
        asset = self.require_asset()
        path = self.pick_image("Reference image of your character") if asset else None
        if path:
            try:
                ch.set_reference(asset, path)
            except StudioError as exc:
                self.show_error(exc)
                return
            self.reload_asset()
            self.services.message.emit("Reference stored. Press 'Create Character' to build the identity.")

    def create_character(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        if asset.output_path(ROLE_ORIGINAL) is None:
            self.choose_reference()
            if self.asset is None or self.asset.output_path(ROLE_ORIGINAL) is None:
                return
            asset = self.asset
        desc, size = self.description.toPlainText().strip(), int(self.size_combo.currentData())
        ref_s, pose_s, seed = self.ref_strength.value(), self.pose_strength.value(), self.seed.seed()
        self.run_job(f"Create character: {asset.name}", lambda c: ch.create_identity(
            self.ctx(), asset, c, description=desc, sprite_size=size, reference_strength=ref_s, pose_strength=pose_s, seed=seed))

    def choose_equipment(self) -> None:
        asset = self.require_asset()
        path = self.pick_image("Equipment reference (weapon / shield)") if asset else None
        if path:
            ch.set_equipment_reference(asset, path)
            if self._identity():
                ch.update_identity(asset, equipment_reference=asset.rel(asset.path("reference", "equipment.png")))
            self.reload_asset()

    def remove_equipment(self) -> None:
        asset = self.require_asset()
        if asset:
            ch.set_equipment_reference(asset, None)
            if self._identity():
                ch.update_identity(asset, equipment_reference="")
            self.reload_asset()

    def add_custom_animation(self) -> None:
        asset = self.require_asset()
        if asset is None or self._identity() is None:
            if asset is not None:
                self.show_error(StudioError("Create the character first.", hint="Press 'Create Character'."))
            return
        name, ok = QInputDialog.getText(self, "Custom animation", "Name (e.g. cast, block, jump):")
        name = "".join(c for c in name.strip().lower().replace(" ", "_") if c.isalnum() or c == "_")
        if not ok or not name:
            return
        frames, ok = QInputDialog.getInt(self, "Custom animation", "Frames:", 4, 1, 32)
        if not ok:
            return
        try:
            ch.ensure_animation(asset, name, frames=frames, directions=self.selected_directions())
        except StudioError as exc:
            self.show_error(exc)
            return
        self.reload_asset()
        idx = self.anim_combo.findData(name)
        if idx >= 0:
            self.anim_combo.setCurrentIndex(idx)
        self.services.message.emit("Custom animation created with idle poses - shape every frame in the Pose Editor.")

    def _ensure_current(self) -> bool:
        """Make sure the selected animation/direction has poses (without generating)."""
        asset = self.asset
        if asset is None or self._identity() is None:
            return False
        name, d = self.current_animation(), self.current_direction()
        info = asset.meta.animations.get(name)
        if info is None or d not in info.directions or info.frames != self.frames_spin.value():
            ch.ensure_animation(asset, name, frames=self.frames_spin.value(), directions=[d])
            self._refresh_frames()
        return True

    def generate_animation(self) -> None:
        identity = self._save_identity_settings()
        if identity is None:
            return
        asset, name, dirs = self.asset, self.current_animation(), self.selected_directions()
        frames, seed = self.frames_spin.value(), self.seed.seed()
        self.run_job(f"Generate {name} ({', '.join(ch.DIRECTION_LABELS[d] for d in dirs)}): {asset.name}",
                     lambda c: ch.generate_animation(self.ctx(), asset, c, name, dirs, frames=frames, seed=seed))

    def regenerate_frame(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        name, d, i = self.current_animation(), self.current_direction(), self.current_frame()
        info = asset.meta.animations.get(name)
        if info is None or d not in info.directions or self.frame_list.count() == 0:
            self.show_error(StudioError("Select a frame first.", hint="Generate the animation, then click a frame under FRAMES."))
            return
        self.run_job(f"Regenerate frame {i + 1:02d} of {name}/{ch.DIRECTION_LABELS.get(d, d)}",
                     lambda c: ch.regenerate_frame(self.ctx(), asset, c, name, d, i))

    # ----------------------------------------------------------------- poses
    def _pose_edited(self, pose) -> None:
        asset = self.asset
        if asset is None or not self._ensure_current():
            return
        try:
            ch.set_pose(asset, self.current_animation(), self.current_direction(), self.current_frame(), pose)
        except StudioError as exc:
            self.show_error(exc)
            return
        self.services.message.emit("Pose saved. Press 'Regenerate Selected Frame' to render the frame with it.")

    def apply_preset(self) -> None:
        asset = self.require_asset()
        if asset is None or not self._ensure_current():
            if asset is not None:
                self.show_error(StudioError("Create the character first.", hint="Press 'Create Character'."))
            return
        name = self.preset_combo.currentData() or self.preset_combo.currentText()
        try:
            pose = ch.apply_preset(asset, self.current_animation(), self.current_direction(), self.current_frame(), name,
                                   self.user_preset_dir())
        except StudioError as exc:
            self.show_error(exc)
            return
        self.pose_editor.set_pose(pose)
        self.tabs.setCurrentIndex(1)

    def mirror_pose(self) -> None:
        if self.pose_editor.pose is not None:
            pose = self.pose_editor.pose.mirrored()
            pose.direction = self.current_direction()
            pose.source = "edited"
            self.pose_editor.set_pose(pose)
            self._pose_edited(pose)

    def reset_pose(self) -> None:
        asset = self.asset
        if asset is None or not self._ensure_current():
            return
        name, d, i = self.current_animation(), self.current_direction(), self.current_frame()
        info = asset.meta.animations[name]
        pose = presets.animation_poses(name, info.frames, d, ch.pose_view(self._identity()), loop=info.loop)[i]
        ch.set_pose(asset, name, d, i, pose)
        self.pose_editor.set_pose(pose)

    def save_preset(self) -> None:
        if self.pose_editor.pose is None or self.user_preset_dir() is None:
            self.show_error(StudioError("There is no pose to save."))
            return
        name, ok = QInputDialog.getText(self, "Save pose preset", "Preset name:")
        if ok and name.strip():
            try:
                presets.save_user_preset(self.user_preset_dir(), name.strip(), self.pose_editor.pose)
            except StudioError as exc:
                self.show_error(exc)
                return
            self._fill_presets()

    # ----------------------------------------------------------------- export
    def export_png(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export frames to folder", str(asset.path("export")))
        if folder:
            try:
                files = ch.export_png_frames(asset, Path(folder))
            except StudioError as exc:
                self.show_error(exc)
                return
            self.services.message.emit(f"{len(files)} frame(s) exported to {folder}")

    def export_sheet(self) -> None:
        asset = self.require_asset()
        if asset is None:
            return
        try:
            sheets = ch.build_sheets(asset)
        except StudioError as exc:
            self.show_error(exc)
            return
        if not sheets:
            self.show_error(StudioError("No animation is complete yet.", hint="Generate all frames of an animation first."))
            return
        folder = QFileDialog.getExistingDirectory(self, "Copy sprite sheets to folder (Cancel = keep in the character folder)",
                                                  str(asset.path("export")))
        if folder and Path(folder).resolve() != asset.path("export").resolve():
            for png in sheets:
                for f in (png, png.with_suffix(".json")):
                    if f.exists():
                        shutil.copyfile(f, Path(folder) / f.name)
            self.services.message.emit(f"{len(sheets)} sprite sheet(s) exported to {folder}")
        else:
            open_path(asset.path("export"))
        self.reload_asset()

    def compare_images(self):
        asset = self.asset
        master_p = asset.output_path(ROLE_MASTER)
        frame_p = self.preview.paths[self.preview.index] if self.preview.paths else None
        if master_p and frame_p:
            master, frame = Image.open(master_p).convert("RGBA"), Image.open(frame_p).convert("RGBA")
            return ([master, frame], ["Identity (reference)", f"{self.current_animation()} frame {self.preview.index + 1}"],
                    compare_to_master(master, frame).summary())
        return super().compare_images()
