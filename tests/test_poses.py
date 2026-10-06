"""Pose system: skeleton projection, templates, presets, OpenPose drawing."""

import numpy as np
import pytest

from pixel_rpg_studio.core.errors import ProjectError
from pixel_rpg_studio.imaging.openpose import JOINTS, draw_pose
from pixel_rpg_studio.poses import presets
from pixel_rpg_studio.poses.skeleton import EDITABLE_JOINTS, Pose2D, PoseParams, PoseView, bbox, project


def test_front_back_side_face_points():
    front, back, left, right = (project(PoseParams(), d) for d in ("s", "n", "w", "e"))
    assert all(front.joints[j] is not None for j in ("nose", "l_eye", "r_eye", "l_ear", "r_ear"))
    assert back.joints["nose"] is None and back.joints["l_eye"] is None and back.joints["l_ear"] is not None
    # facing left shows the character's left side, facing right the right side
    assert left.joints["l_eye"] is not None and left.joints["r_eye"] is None
    assert right.joints["r_eye"] is not None and right.joints["l_eye"] is None
    # front view: the character's left side appears on the image's right
    assert front.joints["l_shoulder"][0] > front.joints["r_shoulder"][0]


def test_same_scale_and_ground_for_all_directions():
    view = PoseView()
    for d in ("s", "n", "w", "e", "sw", "ne"):
        pose = project(PoseParams(), d, view)
        x0, y0, x1, y1 = bbox(pose)
        feet = max(pose.joints["l_ankle"][1], pose.joints["r_ankle"][1])
        assert feet == pytest.approx(view.baseline - 0.035 * view.figure_height / 0.94, abs=0.03)
        assert 0.0 < x0 and x1 < 1.0 and 0.0 < y0


def test_templates_produce_requested_frame_counts_and_motion():
    for name, tpl in presets.TEMPLATES.items():
        poses = presets.animation_poses(name, tpl.frames, "w")
        assert len(poses) == tpl.frames
        for p in poses:
            assert set(EDITABLE_JOINTS) <= set(p.joints)
            assert all(0.0 <= v[0] <= 1.0 and 0.0 <= v[1] <= 1.0 for v in p.joints.values() if v is not None)
    walk = presets.animation_poses("walk", 6, "w")
    assert len({tuple(p.joints["l_ankle"]) for p in walk}) > 3  # legs move
    assert len(presets.animation_poses("attack", 8, "s")) == 8  # adjustable frame count


def test_builtin_presets_requested():
    for name in ("Idle", "Walk 1", "Walk 2", "Walk 3", "Walk 4", "Attack 1", "Attack 2", "Attack 3", "Hurt", "Death"):
        assert name in presets.BUILTIN_PRESETS
    death = presets.preset_pose("Death", "s")
    x0, y0, x1, y1 = bbox(death)
    assert x1 - x0 > y1 - y0  # lying down
    assert 0.0 <= x0 and x1 <= 1.0


def test_pose_editing_and_mirror():
    pose = project(PoseParams(), "w")
    nose = list(pose.joints["nose"])
    head = pose.joints["head"]
    pose.move("head", head[0] + 0.05, head[1])
    assert pose.joints["nose"][0] == pytest.approx(nose[0] + 0.05)  # face follows the head
    assert pose.source == "edited"
    m = pose.mirrored()
    assert m.direction == "e"
    assert m.joints["r_wrist"][0] == pytest.approx(1 - pose.joints["l_wrist"][0])
    assert Pose2D.from_dict(m.to_dict()).joints == m.joints


def test_user_presets_roundtrip(tmp_path):
    pose = presets.preset_pose("Attack 2", "w")
    presets.save_user_preset(tmp_path, "My Slash", pose)
    assert presets.list_user_presets(tmp_path) == ["My Slash"]
    loaded = presets.preset_pose("My Slash", "e", user_dir=tmp_path)
    assert loaded.direction == "e" and loaded.joints["l_wrist"][0] == pytest.approx(1 - pose.joints["r_wrist"][0])
    with pytest.raises(ProjectError):
        presets.save_user_preset(tmp_path, "Idle", pose)
    presets.delete_user_preset(tmp_path, "My Slash")
    assert presets.list_user_presets(tmp_path) == []


def test_openpose_image_colors():
    pose = project(PoseParams(), "s")
    img = np.array(draw_pose(pose.openpose_joints(), 256, 256))
    assert img.shape == (256, 256, 3)
    assert (img.reshape(-1, 3).max(axis=1) > 0).mean() > 0.01
    assert tuple(img[0, 0]) == (0, 0, 0)  # black background like OpenPose training data
    assert len(JOINTS) == 18
