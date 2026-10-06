"""Skeleton model: joint angles → 3D joints → 2D OpenPose pose for a view direction.

A pose is described by a few angles (:class:`PoseParams`: leg swing, knee
bend, arm swing, elbow bend, torso lean...). Forward kinematics turns them
into 3D joint positions of a generic humanoid; an orthographic projection
for the requested direction (front/back/left/right, ready for 8 directions)
gives normalised 2D joint positions (0..1, origin top-left) in the COCO-18
layout that OpenPose ControlNets are trained on.

Every pose of every animation and direction uses the same scale and ground
line, so the generated frames line up on a fixed canvas.

Coordinate system (character space): x = the character's left, y = away from
the viewer (the character faces -y at yaw 0), z = up. Units: body height ≈ 1.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from pixel_rpg_studio.imaging.openpose import JOINTS
from pixel_rpg_studio.project.asset_types import DIRECTIONS

Vec = tuple[float, float, float]

# Joints the user can drag in the pose editor. "head" is the head centre; the
# OpenPose face points (nose, eyes, ears) follow it.
EDITABLE_JOINTS = [
    "head", "neck", "r_shoulder", "l_shoulder", "r_elbow", "l_elbow", "r_wrist", "l_wrist",
    "r_hip", "l_hip", "r_knee", "l_knee", "r_ankle", "l_ankle",
]
FACE_JOINTS = ["nose", "r_eye", "l_eye", "r_ear", "l_ear"]
JOINT_LABELS = {
    "head": "Head", "neck": "Neck", "r_shoulder": "Right shoulder", "l_shoulder": "Left shoulder",
    "r_elbow": "Right elbow", "l_elbow": "Left elbow", "r_wrist": "Right hand", "l_wrist": "Left hand",
    "r_hip": "Right hip", "l_hip": "Left hip", "r_knee": "Right knee", "l_knee": "Left knee",
    "r_ankle": "Right foot", "l_ankle": "Left foot",
}


@dataclass(frozen=True)
class Proportions:
    thigh: float
    shin: float
    ankle: float  # ankle height above the ground
    torso: float  # pelvis -> neck
    head: float  # neck -> head centre
    head_radius: float
    shoulder: float  # half shoulder width
    hip: float  # half hip width
    upper_arm: float
    forearm: float


PROPORTIONS: dict[str, Proportions] = {
    "realistic": Proportions(0.245, 0.235, 0.035, 0.29, 0.10, 0.065, 0.13, 0.08, 0.17, 0.155),
    "heroic": Proportions(0.245, 0.235, 0.035, 0.29, 0.10, 0.06, 0.16, 0.085, 0.175, 0.16),
    "stylized": Proportions(0.21, 0.20, 0.035, 0.26, 0.13, 0.085, 0.14, 0.085, 0.16, 0.145),
    "chibi": Proportions(0.15, 0.14, 0.03, 0.20, 0.17, 0.13, 0.13, 0.08, 0.12, 0.11),
}


@dataclass
class PoseParams:
    """A body pose as angles (degrees) and offsets (fractions of body height)."""

    lean: float = 0.0  # torso lean, + = forward
    head: float = 0.0  # head tilt relative to the torso, + = forward
    bob: float = 0.0  # vertical offset after ground contact, + = up
    shift: float = 0.0  # forward displacement, + = toward the facing direction
    roll: float = 0.0  # whole body falls over in the image plane, + = toward the image's right
    r_leg: float = 0.0  # thigh swing, + = forward
    l_leg: float = 0.0
    r_knee: float = 0.0  # knee bend
    l_knee: float = 0.0
    r_leg_out: float = 3.0  # leg spread
    l_leg_out: float = 3.0
    r_arm: float = 0.0  # upper arm swing, + = forward/up
    l_arm: float = 0.0
    r_arm_out: float = 10.0  # arm abduction (away from the body)
    l_arm_out: float = 10.0
    r_elbow: float = 10.0  # elbow bend
    l_elbow: float = 10.0

    def lerp(self, other: "PoseParams", t: float) -> "PoseParams":
        return PoseParams(**{f.name: getattr(self, f.name) + (getattr(other, f.name) - getattr(self, f.name)) * t for f in fields(self)})

    def mirrored(self) -> "PoseParams":
        d = asdict(self)
        for a, b in (("r_leg", "l_leg"), ("r_knee", "l_knee"), ("r_leg_out", "l_leg_out"), ("r_arm", "l_arm"),
                     ("r_arm_out", "l_arm_out"), ("r_elbow", "l_elbow")):
            d[a], d[b] = d[b], d[a]
        d["roll"] = -d["roll"]
        return PoseParams(**d)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PoseParams":
        names = {f.name for f in fields(cls)}
        return cls(**{k: float(v) for k, v in data.items() if k in names})


@dataclass
class PoseView:
    """How poses are placed on the generation canvas (identical for all frames)."""

    proportions: str = "stylized"
    elevation_deg: float = 10.0  # camera tilt; small values keep OpenPose readable
    figure_height: float = 0.78  # standing height as a fraction of the canvas height
    baseline: float = 0.92  # ground line (fraction of the canvas height from the top)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PoseView":
        data = data or {}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Pose2D:
    """Normalised 2D joints (0..1, origin top-left). ``None`` = not visible."""

    joints: dict[str, list[float] | None]
    direction: str = "s"
    source: str = ""  # e.g. "template:walk", "preset:Walk 2", "edited"
    extra: dict[str, Any] = field(default_factory=dict)  # reserved: weapon/shield points

    def openpose_joints(self) -> dict[str, list[float] | None]:
        return {name: self.joints.get(name) for name in JOINTS}

    def move(self, name: str, u: float, v: float) -> None:
        """Move one joint; moving the head carries the face points along."""
        u, v = min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
        old = self.joints.get(name)
        self.joints[name] = [u, v]
        if name == "head" and old is not None:
            du, dv = u - old[0], v - old[1]
            for f in FACE_JOINTS:
                p = self.joints.get(f)
                if p is not None:
                    self.joints[f] = [p[0] + du, p[1] + dv]
        self.source = "edited"

    def mirrored(self) -> "Pose2D":
        swapped: dict[str, list[float] | None] = {}
        for name, p in self.joints.items():
            other = "l_" + name[2:] if name.startswith("r_") else ("r_" + name[2:] if name.startswith("l_") else name)
            swapped[other] = [1.0 - p[0], p[1]] if p is not None else None
        mirror_dir = {"w": "e", "e": "w", "sw": "se", "se": "sw", "nw": "ne", "ne": "nw"}
        return Pose2D(swapped, mirror_dir.get(self.direction, self.direction), self.source, dict(self.extra))

    def copy(self) -> "Pose2D":
        return Pose2D({k: (list(v) if v is not None else None) for k, v in self.joints.items()}, self.direction, self.source,
                      dict(self.extra))

    def to_dict(self) -> dict[str, Any]:
        return {"direction": self.direction, "source": self.source, "joints": self.joints, "extra": self.extra}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Pose2D":
        joints = {str(k): ([float(v[0]), float(v[1])] if v is not None else None) for k, v in (data.get("joints") or {}).items()}
        return cls(joints, str(data.get("direction", "s")), str(data.get("source", "")), dict(data.get("extra") or {}))


# ------------------------------------------------------------------ maths
def _add(a: Vec, b: Vec, s: float = 1.0) -> Vec:
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)


def _limb_dir(swing: float, out: float, side: float) -> Vec:
    """Unit vector of a limb hanging down, swung forward by ``swing`` and
    spread sideways by ``out`` (degrees); side = +1 left, -1 right."""
    s, o = math.radians(swing), math.radians(out)
    return (side * math.sin(o), -math.sin(s) * math.cos(o), -math.cos(s) * math.cos(o))


def joints_3d(params: PoseParams, proportions: str = "stylized") -> tuple[dict[str, Vec], dict[str, Any]]:
    """Forward kinematics. Returns 3D joints (incl. face points) and head info."""
    P = PROPORTIONS.get(proportions, PROPORTIONS["stylized"])
    j: dict[str, Vec] = {}
    pelvis: Vec = (0.0, 0.0, P.thigh + P.shin + P.ankle)
    for side, pre in ((-1.0, "r_"), (1.0, "l_")):
        hip = _add(pelvis, (side * P.hip, 0.0, 0.0))
        swing, knee = getattr(params, pre + "leg"), getattr(params, pre + "knee")
        out = getattr(params, pre + "leg_out")
        k = _add(hip, _limb_dir(swing, out, side), P.thigh)
        j[pre + "hip"], j[pre + "knee"] = hip, k
        j[pre + "ankle"] = _add(k, _limb_dir(swing - knee, out, side), P.shin)
    lean = math.radians(params.lean)
    up = (0.0, -math.sin(lean), math.cos(lean))
    neck = _add(pelvis, up, P.torso)
    j["neck"] = neck
    for side, pre in ((-1.0, "r_"), (1.0, "l_")):
        sh = _add(_add(neck, (side * P.shoulder, 0.0, 0.0)), up, -0.025)
        swing = getattr(params, pre + "arm") + params.lean
        out = getattr(params, pre + "arm_out")
        el = _add(sh, _limb_dir(swing, out, side), P.upper_arm)
        j[pre + "shoulder"], j[pre + "elbow"] = sh, el
        j[pre + "wrist"] = _add(el, _limb_dir(swing + getattr(params, pre + "elbow"), out, side), P.forearm)
    tilt = lean + math.radians(params.head)
    hup = (0.0, -math.sin(tilt), math.cos(tilt))
    fwd = (0.0, -math.cos(tilt), -math.sin(tilt))
    c = _add(neck, hup, P.head)
    r = P.head_radius
    j["head"] = c
    j["nose"] = _add(_add(c, fwd, 0.9 * r), hup, -0.15 * r)
    for side, pre in ((-1.0, "r_"), (1.0, "l_")):
        j[pre + "eye"] = _add(_add(_add(c, fwd, 0.75 * r), (side * 0.38 * r, 0.0, 0.0)), hup, 0.15 * r)
        j[pre + "ear"] = _add(_add(c, (side * 0.95 * r, 0.0, 0.0)), hup, 0.05 * r)
    # ground contact: the lowest point rests on the ground, then bob/shift
    lowest = min(p[2] for n, p in j.items() if n not in FACE_JOINTS and n != "head")
    dz = P.ankle - lowest + params.bob
    for n, p in j.items():
        j[n] = (p[0], p[1] - params.shift, p[2] + dz)
    return j, {"head_radius": r, "forward": fwd}


def standing_height(proportions: str = "stylized") -> float:
    j, info = joints_3d(PoseParams(), proportions)
    return j["head"][2] + info["head_radius"]


def project(params: PoseParams, direction: str, view: PoseView | None = None) -> Pose2D:
    """2D OpenPose pose of ``params`` seen from ``direction`` (s, w, n, e, sw...)."""
    view = view or PoseView()
    j3, info = joints_3d(params, view.proportions)
    yaw = math.radians(-DIRECTIONS[direction].yaw_deg if direction in DIRECTIONS else 0.0)
    cy, sy = math.cos(yaw), math.sin(yaw)
    e = math.radians(view.elevation_deg)
    k = view.figure_height / standing_height(view.proportions)

    def rot(p: Vec) -> Vec:
        return (p[0] * cy - p[1] * sy, p[0] * sy + p[1] * cy, p[2])

    def to2d(p: Vec) -> list[float]:
        x, depth, z = rot(p)
        up = depth * math.sin(e) + z * math.cos(e)
        return [round(0.5 + x * k, 5), round(view.baseline - up * k, 5)]

    if abs(params.roll) > 1e-6:
        j3 = _fall(j3, params, yaw, view.proportions)
        cy, sy = 1.0, 0.0  # _fall returned view-space points
    joints: dict[str, list[float] | None] = {n: to2d(p) for n, p in j3.items()}
    # face visibility from the head's facing direction
    cy, sy = math.cos(yaw), math.sin(yaw)
    j3, _ = joints_3d(params, view.proportions)
    c = rot(j3["head"])
    r = info["head_radius"]
    facing = -rot(info["forward"])[1]  # 1 = toward the viewer, -1 = away
    for name in FACE_JOINTS:
        depth = rot(j3[name])[1] - c[1]
        if name == "nose":
            visible = facing > -0.3
        elif name.endswith("eye"):
            visible = facing > -0.3 and depth < 0.2 * r
        else:
            visible = depth < 0.4 * r
        if not visible:
            joints[name] = None
    return Pose2D(joints, direction)


def _fall(j3: dict[str, Vec], params: PoseParams, yaw: float, proportions: str) -> dict[str, Vec]:
    """Rotate the body in the image plane around the feet (falling over), so a
    lying pose reads clearly from every direction; then re-ground and keep
    the figure horizontally centred on the canvas."""
    P = PROPORTIONS.get(proportions, PROPORTIONS["stylized"])
    cy, sy = math.cos(yaw), math.sin(yaw)
    view = {n: (p[0] * cy - p[1] * sy, p[0] * sy + p[1] * cy, p[2]) for n, p in j3.items()}
    a = math.radians(params.roll)
    ca, sa = math.cos(a), math.sin(a)
    px = (view["r_ankle"][0] + view["l_ankle"][0]) / 2
    out = {n: (px + (p[0] - px) * ca + p[2] * sa, p[1], -(p[0] - px) * sa + p[2] * ca) for n, p in view.items()}
    body = [p for n, p in out.items() if n not in FACE_JOINTS and n != "head"]
    dz = P.ankle - min(p[2] for p in body)
    xs = [p[0] for p in body]
    dx = -(max(xs) + min(xs)) / 2 * min(1.0, abs(params.roll) / 90.0)
    return {n: (p[0] + dx, p[1], p[2] + dz) for n, p in out.items()}


def bbox(pose: Pose2D) -> tuple[float, float, float, float] | None:
    pts = [p for p in pose.joints.values() if p is not None]
    if not pts:
        return None
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)
