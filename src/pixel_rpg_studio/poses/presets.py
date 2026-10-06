"""Pose presets and animation templates.

Built-in presets (Idle, Walk 1-4, Run 1-4, Attack 1-3, Hurt, Death...) are
:class:`PoseParams`, so they work for every view direction. An animation
template is a list of (phase, preset) keys; frames are sampled from it with
the frame count the user chooses. Poses edited in the pose editor can be
saved as user presets (2D, per project) and applied to any frame.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pixel_rpg_studio.core.config import write_json_atomic
from pixel_rpg_studio.core.errors import ProjectError
from pixel_rpg_studio.core.paths import safe_filename
from pixel_rpg_studio.poses.skeleton import Pose2D, PoseParams, PoseView, project

P = PoseParams

_WALK_1 = P(r_leg=24, r_knee=6, l_leg=-20, l_knee=12, r_arm=-18, l_arm=18, r_elbow=18, l_elbow=18)
_WALK_2 = P(r_leg=-2, r_knee=2, l_leg=8, l_knee=42, r_arm=0, l_arm=0, r_elbow=14, l_elbow=14, bob=0.014)
_RUN_1 = P(lean=12, r_leg=36, r_knee=22, l_leg=-30, l_knee=55, r_arm=-40, l_arm=40, r_elbow=80, l_elbow=80, bob=0.0)
_RUN_2 = P(lean=12, r_leg=-6, r_knee=16, l_leg=24, l_knee=100, r_arm=-5, l_arm=5, r_elbow=85, l_elbow=85, bob=0.03)

BUILTIN_PRESETS: dict[str, PoseParams] = {
    "Idle": P(r_arm=4, l_arm=-2, r_elbow=12, l_elbow=12),
    "Idle 2": P(r_arm=6, l_arm=0, r_elbow=18, l_elbow=18, r_knee=5, l_knee=5, head=3, bob=-0.006),
    "Walk 1": _WALK_1,
    "Walk 2": _WALK_2,
    "Walk 3": _WALK_1.mirrored(),
    "Walk 4": _WALK_2.mirrored(),
    "Run 1": _RUN_1,
    "Run 2": _RUN_2,
    "Run 3": _RUN_1.mirrored(),
    "Run 4": _RUN_2.mirrored(),
    "Attack 1": P(lean=-5, head=-5, r_arm=160, r_arm_out=15, r_elbow=40, l_arm=30, l_elbow=50, r_leg=10, l_leg=-10, r_knee=8, l_knee=8),
    "Attack 2": P(lean=15, r_arm=85, r_elbow=5, l_arm=20, l_elbow=40, r_leg=25, r_knee=20, l_leg=-20, l_knee=10, shift=0.03),
    "Attack 3": P(lean=20, r_arm=25, r_arm_out=20, r_elbow=10, l_arm=0, l_elbow=30, r_leg=30, r_knee=30, l_leg=-25, l_knee=8, shift=0.04),
    "Hurt": P(lean=-15, head=-15, r_arm=25, r_arm_out=30, r_elbow=40, l_arm=25, l_arm_out=30, l_elbow=40, r_knee=12, l_knee=12, shift=-0.03),
    "Death 1": P(lean=30, head=20, r_leg=20, r_knee=110, l_leg=-10, l_knee=90, r_arm=10, r_arm_out=15, l_arm=10, l_arm_out=15),
    "Death": P(roll=-88, r_arm=10, r_arm_out=35, l_arm=10, l_arm_out=35, r_elbow=20, l_elbow=20, head=-10),
}


@dataclass(frozen=True)
class AnimationTemplate:
    name: str
    keys: tuple[tuple[float, str], ...]  # (phase 0..1, preset name)
    frames: int
    fps: int
    loop: bool
    prompt: str  # words describing the action for the image model


TEMPLATES: dict[str, AnimationTemplate] = {
    t.name: t
    for t in (
        AnimationTemplate("idle", ((0.0, "Idle"), (0.5, "Idle 2")), 4, 6, True, "standing idle"),
        AnimationTemplate("walk", ((0.0, "Walk 1"), (0.25, "Walk 2"), (0.5, "Walk 3"), (0.75, "Walk 4")), 6, 10, True, "walking"),
        AnimationTemplate("run", ((0.0, "Run 1"), (0.25, "Run 2"), (0.5, "Run 3"), (0.75, "Run 4")), 6, 12, True, "running"),
        AnimationTemplate("attack", ((0.0, "Idle"), (0.3, "Attack 1"), (0.6, "Attack 2"), (0.85, "Attack 3"), (1.0, "Idle")),
                          6, 12, False, "attacking, swinging the weapon"),
        AnimationTemplate("hurt", ((0.0, "Hurt"), (0.6, "Hurt"), (1.0, "Idle")), 3, 10, False, "getting hit, recoiling"),
        AnimationTemplate("death", ((0.0, "Hurt"), (0.4, "Death 1"), (1.0, "Death")), 6, 8, False, "falling down, defeated"),
    )
}
STANDARD_ANIMATIONS = tuple(TEMPLATES)
CUSTOM_TEMPLATE = AnimationTemplate("custom", ((0.0, "Idle"),), 4, 8, True, "")


def template(name: str) -> AnimationTemplate:
    return TEMPLATES.get(name, CUSTOM_TEMPLATE)


def params_at(tpl: AnimationTemplate, phase: float) -> PoseParams:
    keys = list(tpl.keys)
    if len(keys) == 1:
        return BUILTIN_PRESETS[keys[0][1]]
    if tpl.loop:
        keys = keys + [(1.0, keys[0][1])]
    phase = phase % 1.0 if tpl.loop else min(1.0, max(0.0, phase))
    for (p0, n0), (p1, n1) in zip(keys, keys[1:]):
        if p0 <= phase <= p1:
            t = 0.0 if p1 == p0 else (phase - p0) / (p1 - p0)
            return BUILTIN_PRESETS[n0].lerp(BUILTIN_PRESETS[n1], t)
    return BUILTIN_PRESETS[keys[-1][1]]


def frame_phase(index: int, count: int, loop: bool) -> float:
    if loop:
        return index / max(1, count)
    return index / max(1, count - 1)


def animation_poses(name: str, frames: int, direction: str, view: PoseView | None = None, loop: bool | None = None) -> list[Pose2D]:
    """2D poses for every frame of an animation in one direction."""
    tpl = template(name)
    loop = tpl.loop if loop is None else loop
    poses = []
    for i in range(frames):
        pose = project(params_at(tpl, frame_phase(i, frames, loop)), direction, view)
        pose.source = f"template:{tpl.name}"
        poses.append(pose)
    return poses


def preset_pose(name: str, direction: str, view: PoseView | None = None, user_dir: Path | None = None) -> Pose2D:
    """A built-in preset projected for ``direction``, or a saved user preset."""
    if name in BUILTIN_PRESETS:
        pose = project(BUILTIN_PRESETS[name], direction, view)
        pose.source = f"preset:{name}"
        return pose
    if user_dir is not None:
        pose = load_user_preset(user_dir, name)
        if pose.direction != direction and {pose.direction, direction} == {"w", "e"}:
            pose = pose.mirrored()
        pose.direction = direction
        pose.source = f"preset:{name}"
        return pose
    raise ProjectError(f"Unknown pose preset '{name}'.")


# ------------------------------------------------------------ user presets
def _preset_path(user_dir: Path, name: str) -> Path:
    return Path(user_dir) / f"{safe_filename(name, fallback='pose')}.json"


def list_user_presets(user_dir: Path | None) -> list[str]:
    if user_dir is None or not Path(user_dir).is_dir():
        return []
    names = []
    for f in sorted(Path(user_dir).glob("*.json")):
        try:
            names.append(str(json.loads(f.read_text(encoding="utf-8")).get("name") or f.stem))
        except (OSError, ValueError):
            continue
    return names


def save_user_preset(user_dir: Path, name: str, pose: Pose2D) -> Path:
    if not name.strip():
        raise ProjectError("The preset needs a name.")
    if name.strip() in BUILTIN_PRESETS:
        raise ProjectError(f"'{name}' is a built-in preset name.", hint="Choose another name.")
    path = _preset_path(user_dir, name.strip())
    write_json_atomic(path, {"name": name.strip(), **pose.to_dict()})
    return path


def load_user_preset(user_dir: Path, name: str) -> Pose2D:
    for f in Path(user_dir).glob("*.json") if Path(user_dir).is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (data.get("name") or f.stem) == name:
            return Pose2D.from_dict(data)
    raise ProjectError(f"Pose preset '{name}' was not found.")


def delete_user_preset(user_dir: Path, name: str) -> None:
    for f in Path(user_dir).glob("*.json") if Path(user_dir).is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (data.get("name") or f.stem) == name:
            f.unlink()
            return
