"""Pixel RPG Asset Studio - Blender worker script.

Runs INSIDE Blender (``blender --background --factory-startup --python
studio_blender.py -- job.json``). It is generated/driven by the desktop app;
users never need to open Blender for normal operation.

Modes
-----
``prepare``  import a model, clean it up, normalise scale/orientation, project
             the concept image as colours, (optionally) build an automatic
             humanoid rig, compute stable camera framing, save a .blend and a
             cleaned GLB. The .blend can be opened and corrected manually
             (rig, weights, shape) - later renders use the corrected file.
``render``   open the prepared .blend and render frames for the requested
             directions/animations with a fixed orthographic camera and
             deterministic "toon" shading (works with Cycles and EEVEE).

Every run writes a JSON report (``report_path``) with warnings and results.
Compatible with Blender 3.6 LTS - 4.x.
"""

import json
import math
import os
import sys
import time
import traceback

import bmesh
import bpy
from mathutils import Matrix, Quaternion, Vector

try:
    import numpy as np
except ImportError:  # pragma: no cover - Blender always bundles numpy
    np = None

REPORT = {"ok": False, "warnings": [], "errors": [], "frames": [], "blender_version": bpy.app.version_string}

# Canonical bone names of the procedural humanoid rig.
HUMANOID_BONES = [
    "hips", "spine", "chest", "neck", "head",
    "upper_arm.L", "forearm.L", "hand.L", "upper_arm.R", "forearm.R", "hand.R",
    "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R",
]

# Mapping from common external rig naming schemes to canonical names, so the
# procedural animations also drive imported (e.g. Mixamo) rigs.
BONE_ALIASES = {
    "hips": ["mixamorig:Hips", "Hips", "pelvis", "Pelvis"],
    "spine": ["mixamorig:Spine", "Spine", "spine_01"],
    "chest": ["mixamorig:Spine2", "mixamorig:Spine1", "Chest", "spine_03", "spine_02"],
    "neck": ["mixamorig:Neck", "Neck", "neck_01"],
    "head": ["mixamorig:Head", "Head"],
    "upper_arm.L": ["mixamorig:LeftArm", "LeftArm", "upperarm_l", "UpperArm.L"],
    "forearm.L": ["mixamorig:LeftForeArm", "LeftForeArm", "lowerarm_l"],
    "hand.L": ["mixamorig:LeftHand", "LeftHand", "hand_l"],
    "upper_arm.R": ["mixamorig:RightArm", "RightArm", "upperarm_r", "UpperArm.R"],
    "forearm.R": ["mixamorig:RightForeArm", "RightForeArm", "lowerarm_r"],
    "hand.R": ["mixamorig:RightHand", "RightHand", "hand_r"],
    "thigh.L": ["mixamorig:LeftUpLeg", "LeftUpLeg", "thigh_l"],
    "shin.L": ["mixamorig:LeftLeg", "LeftLeg", "calf_l"],
    "foot.L": ["mixamorig:LeftFoot", "LeftFoot", "foot_l"],
    "thigh.R": ["mixamorig:RightUpLeg", "RightUpLeg", "thigh_r"],
    "shin.R": ["mixamorig:RightLeg", "RightLeg", "calf_r"],
    "foot.R": ["mixamorig:RightFoot", "RightFoot", "foot_r"],
}

ROOT_NAME = "StudioRoot"
RIG_NAME = "StudioRig"
MODEL_NAME = "StudioModel"
CAMERA_NAME = "StudioCamera"


def warn(msg):
    print("[studio] WARNING:", msg)
    REPORT["warnings"].append(msg)


def info(msg):
    print("[studio]", msg)


# ----------------------------------------------------------------- utilities
def ensure_object_mode():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def select_only(objs, active=None):
    ensure_object_mode()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path, forward_axis="NEGATIVE_Y", up_axis="Z")
        else:  # pragma: no cover - very old Blender
            bpy.ops.import_scene.obj(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=path)
        else:
            bpy.ops.import_mesh.stl(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    else:
        raise RuntimeError("Unsupported model format '%s' (use GLB, GLTF, OBJ, FBX, STL or PLY)" % ext)
    new = [o for o in bpy.data.objects if o not in before]
    if not any(o.type == "MESH" for o in new):
        raise RuntimeError("The model file contains no mesh objects: %s" % path)
    return new


def world_vertices(obj, depsgraph=None, evaluated=True):
    """World-space vertex coordinates as an (N, 3) numpy array."""
    if evaluated:
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        mw = eval_obj.matrix_world.copy()
    else:
        mesh = obj.data
        mw = obj.matrix_world.copy()
    n = len(mesh.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    if evaluated:
        eval_obj.to_mesh_clear()
    m = np.array(mw)
    return co @ m[:3, :3].T + m[:3, 3]


# --------------------------------------------------------------- cleanup
def join_meshes(objs):
    meshes = [o for o in objs if o.type == "MESH"]
    if len(meshes) > 1:
        select_only(meshes, meshes[0])
        bpy.ops.object.join()
    mesh_obj = bpy.context.view_layer.objects.active if len(meshes) > 1 else meshes[0]
    return mesh_obj


def apply_transforms(obj):
    select_only([obj])
    # Unparent while keeping the world transform, then bake it into the mesh.
    if obj.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def cleanup_mesh(obj, cfg):
    stats = {"faces_before": len(obj.data.polygons)}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    merge = float(cfg.get("merge_distance", 0.0001))
    if merge > 0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge)
    # Remove small floating islands (common in AI-generated meshes).
    min_fraction = float(cfg.get("min_island_fraction", 0.01))
    if min_fraction > 0 and len(bm.faces) > 0:
        bm.faces.ensure_lookup_table()
        seen = set()
        islands = []
        for f in bm.faces:
            if f.index in seen:
                continue
            stack = [f]
            island = []
            seen.add(f.index)
            while stack:
                cur = stack.pop()
                island.append(cur)
                for e in cur.edges:
                    for nf in e.link_faces:
                        if nf.index not in seen:
                            seen.add(nf.index)
                            stack.append(nf)
            islands.append(island)
        total = len(bm.faces)
        small = [isl for isl in islands if len(isl) < total * min_fraction]
        if small and len(small) < len(islands):
            remove_faces = [f for isl in small for f in isl]
            bmesh.ops.delete(bm, geom=remove_faces, context="FACES")
            stats["removed_islands"] = len(small)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    target = int(cfg.get("decimate_faces", 12000))
    faces = len(obj.data.polygons)
    if target > 0 and faces > target:
        mod = obj.modifiers.new("StudioDecimate", "DECIMATE")
        mod.ratio = max(0.001, target / faces)
        select_only([obj])
        bpy.ops.object.modifier_apply(modifier=mod.name)
    stats["faces_after"] = len(obj.data.polygons)
    values = [True] * len(obj.data.polygons)
    obj.data.polygons.foreach_set("use_smooth", values)
    return stats


def normalize(obj, cfg):
    """Rotate by the facing correction, scale to target height, feet on the ground, centred."""
    yaw_fix = float(cfg.get("facing_correction_deg", 0.0))
    if yaw_fix:
        obj.rotation_euler[2] += math.radians(yaw_fix)
    apply_transforms(obj)
    co = world_vertices(obj, evaluated=False)
    mn, mx = co.min(axis=0), co.max(axis=0)
    size = mx - mn
    mode = cfg.get("scale_mode", "height")
    target = float(cfg.get("target_size", 2.0))
    ref = size[2] if mode == "height" else max(size)
    if ref <= 1e-9:
        raise RuntimeError("The model has zero size.")
    s = target / ref
    center = (mn + mx) / 2
    obj.location = (-center[0] * s, -center[1] * s, -mn[2] * s)
    obj.scale = (s, s, s)
    apply_transforms(obj)
    co = world_vertices(obj, evaluated=False)
    return {"dimensions": [float(v) for v in (co.max(axis=0) - co.min(axis=0))]}


# ------------------------------------------------------------- materials
def project_front_uvs(obj):
    """Planar UVs from the front view: x -> u, z -> v over the bounding box."""
    mesh = obj.data
    co = world_vertices(obj, evaluated=False)
    mn, mx = co.min(axis=0), co.max(axis=0)
    span_x = max(1e-9, mx[0] - mn[0])
    span_z = max(1e-9, mx[2] - mn[2])
    uv_layer = mesh.uv_layers.new(name="StudioFrontProjection")
    n_loops = len(mesh.loops)
    vidx = np.empty(n_loops, dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", vidx)
    loop_co = co[vidx]
    uv = np.empty((n_loops, 2), dtype=np.float64)
    uv[:, 0] = (loop_co[:, 0] - mn[0]) / span_x
    uv[:, 1] = (loop_co[:, 2] - mn[2]) / span_z
    uv_layer.data.foreach_set("uv", uv.ravel())
    mesh.uv_layers.active = uv_layer
    return uv_layer.name


def build_toon_material(name, color_source, shading, light_dir, image_path=None, flat_color=(0.7, 0.7, 0.7), uv_name=None, base_image=None):
    """Engine-independent toon material.

    Shading = dot(world normal, fixed light direction) -> colour ramp (bands)
    -> multiplied with the base colour -> Emission. No scene lights are
    involved, so the result is identical in Cycles and EEVEE, deterministic
    and fixed relative to the camera (the model is rotated, never the light).
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emission = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(emission.outputs["Emission"], out.inputs["Surface"])

    # Base colour
    if color_source == "image" and (image_path or base_image):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = base_image or bpy.data.images.load(image_path)
        tex.interpolation = "Closest"
        tex.extension = "EXTEND"
        if uv_name:
            uvn = nt.nodes.new("ShaderNodeUVMap")
            uvn.uv_map = uv_name
            nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        base = tex.outputs["Color"]
    elif color_source == "keep":
        base = None
    else:
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = (flat_color[0], flat_color[1], flat_color[2], 1.0)
        base = rgb.outputs[0]

    style = shading.get("style", "cel")
    if style == "flat":
        if base is not None:
            nt.links.new(base, emission.inputs["Color"])
        return mat

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    light = nt.nodes.new("ShaderNodeCombineXYZ")
    light.inputs[0].default_value, light.inputs[1].default_value, light.inputs[2].default_value = light_dir
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    nt.links.new(light.outputs["Vector"], dot.inputs[1])
    # map [-1, 1] -> [0, 1]
    remap = nt.nodes.new("ShaderNodeMapRange")
    remap.inputs["From Min"].default_value = -1.0
    remap.inputs["From Max"].default_value = 1.0
    nt.links.new(dot.outputs["Value"], remap.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    bands = max(1, int(shading.get("bands", 3)))
    ambient = float(shading.get("ambient", 0.45))
    ramp.color_ramp.interpolation = "CONSTANT" if style == "cel" else "LINEAR"
    elements = ramp.color_ramp.elements
    # positions/values for N bands from ambient (dark) to 1.0 (lit)
    positions = [0.0] + [0.35 + 0.4 * (i / max(1, bands - 1)) for i in range(1, bands)] if bands > 1 else [0.0]
    values = [ambient + (1.0 - ambient) * (i / max(1, bands - 1)) for i in range(bands)] if bands > 1 else [1.0]
    while len(elements) < len(positions):
        elements.new(0.5)
    while len(elements) > len(positions):
        elements.remove(elements[-1])
    for el, pos, val in zip(elements, positions, values):
        el.position = pos
        el.color = (val, val, val, 1.0)
    nt.links.new(remap.outputs["Result"], ramp.inputs["Fac"])
    if base is None:
        nt.links.new(ramp.outputs["Color"], emission.inputs["Color"])
        return mat
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs["Fac"].default_value = 1.0
    nt.links.new(base, mix.inputs["Color1"])
    nt.links.new(ramp.outputs["Color"], mix.inputs["Color2"])
    nt.links.new(mix.outputs["Color"], emission.inputs["Color"])
    return mat


def light_vector(azimuth_deg, elevation_deg):
    """Light direction in camera-relative terms for a camera looking along +Y.

    The camera rotates around the model only by the fixed perspective yaw, so
    expressing the light in this frame keeps it constant on screen.
    """
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    # right = +X, towards camera = -Y, up = +Z
    v = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    return v.normalized()


def apply_materials(obj, cfg, light_dir):
    tex_cfg = cfg.get("texture", {})
    mode = tex_cfg.get("mode", "project")
    shading = cfg.get("shading", {"style": "cel", "bands": 3})
    if mode == "keep":
        # Convert existing materials' base colour textures into toon materials where possible.
        new_mats = []
        for slot in obj.material_slots:
            src = slot.material
            image = None
            color = (0.7, 0.7, 0.7)
            if src and src.use_nodes:
                for n in src.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        image = n.image
                        break
                    if n.type == "BSDF_PRINCIPLED":
                        c = n.inputs["Base Color"].default_value
                        color = (c[0], c[1], c[2])
            uv = obj.data.uv_layers.active.name if obj.data.uv_layers.active else None
            mat = build_toon_material("Studio_" + (src.name if src else "mat"), "image" if image else "flat", shading, light_dir,
                                      base_image=image, flat_color=color, uv_name=uv)
            new_mats.append(mat)
        if not new_mats:
            new_mats.append(build_toon_material("StudioFlat", "flat", shading, light_dir))
            obj.data.materials.append(new_mats[0])
        else:
            for slot, mat in zip(obj.material_slots, new_mats):
                slot.material = mat
        return "keep"
    obj.data.materials.clear()
    if mode == "project" and tex_cfg.get("image") and os.path.exists(tex_cfg["image"]):
        uv = project_front_uvs(obj)
        mat = build_toon_material("StudioProjected", "image", shading, light_dir, image_path=tex_cfg["image"], uv_name=uv)
    else:
        if mode == "project":
            warn("No concept image for colour projection; using a flat colour.")
        mat = build_toon_material("StudioFlat", "flat", shading, light_dir, flat_color=tex_cfg.get("flat_color", (0.7, 0.7, 0.7)))
    obj.data.materials.append(mat)
    return mode


# ------------------------------------------------------------------- rig
def _x_extent_at(co, z, band):
    sel = co[np.abs(co[:, 2] - z) < band]
    if len(sel) == 0:
        return 0.0, 0.0
    return float(sel[:, 0].min()), float(sel[:, 0].max())


def build_humanoid_rig(mesh_obj):
    """Create a simple humanoid armature from the mesh proportions.

    Assumes a character standing upright (T-pose or A-pose), facing -Y, feet at
    z=0. Arm positions are estimated from the mesh width profile. This is a
    heuristic: the report tells the user how well the skinning worked, and
    the saved .blend can be corrected manually.
    """
    co = world_vertices(mesh_obj, evaluated=False)
    H = float(co[:, 2].max())
    band = H * 0.03
    # Find shoulder/arm height = widest slice in the upper body.
    zs = np.linspace(0.55 * H, 0.9 * H, 71)
    widths = np.array([(lambda e: e[1] - e[0])(_x_extent_at(co, z, band * 0.5)) for z in zs])
    best_w = float(widths.max())
    # centre of the band where the body is (nearly) widest = arm line
    best_z = float(zs[widths >= 0.97 * best_w].mean())
    torso_lo, torso_hi = _x_extent_at(co, 0.6 * H, band)
    torso_half = max(0.08 * H, (torso_hi - torso_lo) / 2)
    hip_lo, hip_hi = _x_extent_at(co, 0.45 * H, band)
    hip_half = max(0.06 * H, (hip_hi - hip_lo) / 2)
    arm_tip_l = max(float(co[:, 0].max()), torso_half * 1.2)
    arm_tip_r = min(float(co[:, 0].min()), -torso_half * 1.2)
    t_pose = best_w > 0.65 * H
    shoulder_z = best_z if t_pose else 0.8 * H
    sx = torso_half * 0.85

    arm_data = bpy.data.armatures.new(RIG_NAME)
    rig = bpy.data.objects.new(RIG_NAME, arm_data)
    bpy.context.scene.collection.objects.link(rig)
    select_only([rig])
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(name, head, tail, parent=None, connect=False):
        b = eb.new(name)
        b.head, b.tail = Vector(head), Vector(tail)
        b.roll = 0.0
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        return b

    bone("hips", (0, 0, 0.48 * H), (0, 0, 0.58 * H))
    bone("spine", (0, 0, 0.58 * H), (0, 0, 0.68 * H), "hips", True)
    bone("chest", (0, 0, 0.68 * H), (0, 0, 0.8 * H), "spine", True)
    bone("neck", (0, 0, 0.8 * H), (0, 0, 0.86 * H), "chest", True)
    bone("head", (0, 0, 0.86 * H), (0, 0, H), "neck", True)
    for side, sign, tip in (("L", 1, arm_tip_l), ("R", -1, arm_tip_r)):
        if t_pose:
            reach = abs(tip) - sx
            p0 = (sign * sx, 0, shoulder_z)
            p1 = (sign * (sx + reach * 0.45), 0, shoulder_z)
            p2 = (sign * (sx + reach * 0.85), 0, shoulder_z)
            p3 = (sign * (sx + reach), 0, shoulder_z)
        else:  # arms down / A-pose: bones go down along the body side
            ax = max(sx * 1.1, abs(tip) * 0.85)
            p0 = (sign * sx, 0, shoulder_z)
            p1 = (sign * ax, 0, shoulder_z - 0.17 * H)
            p2 = (sign * ax, 0, shoulder_z - 0.32 * H)
            p3 = (sign * ax, 0, shoulder_z - 0.4 * H)
        bone("upper_arm." + side, p0, p1, "chest")
        bone("forearm." + side, p1, p2, "upper_arm." + side, True)
        bone("hand." + side, p2, p3, "forearm." + side, True)
        lx = sign * hip_half * 0.55
        bone("thigh." + side, (lx, 0, 0.48 * H), (lx, 0, 0.27 * H), "hips")
        bone("shin." + side, (lx, 0, 0.27 * H), (lx, 0, 0.05 * H), "thigh." + side, True)
        bone("foot." + side, (lx, 0, 0.05 * H), (lx, -0.08 * H, 0.0), "shin." + side, True)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig["studio_t_pose"] = bool(t_pose)
    return rig, {"t_pose": bool(t_pose), "shoulder_height": shoulder_z / H}


def unweighted_vertex_count(mesh_obj):
    count = 0
    for v in mesh_obj.data.vertices:
        if not any(g.weight > 1e-4 for g in v.groups):
            count += 1
    return count


def skin_mesh(mesh_obj, rig):
    """Automatic (heat) weights; falls back to envelope weights if they fail."""
    select_only([mesh_obj, rig], rig)
    method = "automatic"
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except RuntimeError as exc:
        warn("Automatic weights failed: %s" % exc)
    n = len(mesh_obj.data.vertices)
    missing = unweighted_vertex_count(mesh_obj)
    if missing > 0.05 * n:
        warn("Automatic weights left %d of %d vertices unweighted; falling back to envelope weights. "
             "For best results fix the weights manually in the saved .blend file." % (missing, n))
        mesh_obj.vertex_groups.clear()
        for m in [m for m in mesh_obj.modifiers if m.type == "ARMATURE"]:
            mesh_obj.modifiers.remove(m)
        select_only([mesh_obj], mesh_obj)
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
        for b in rig.data.bones:
            b.envelope_distance = 0.25 * rig.dimensions.z
            b.head_radius = b.tail_radius = 0.06 * rig.dimensions.z
        select_only([mesh_obj, rig], rig)
        bpy.ops.object.parent_set(type="ARMATURE_ENVELOPE")
        method = "envelope"
        missing = unweighted_vertex_count(mesh_obj)
    return {"weights": method, "unweighted_vertices": missing, "vertices": n}


def find_armature():
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    return arms[0] if arms else None


def bone_map(rig):
    """canonical name -> actual pose bone name present in the rig."""
    names = {b.name for b in rig.pose.bones}
    mapping = {}
    for canonical in HUMANOID_BONES:
        if canonical in names:
            mapping[canonical] = canonical
            continue
        for alias in BONE_ALIASES.get(canonical, []):
            if alias in names:
                mapping[canonical] = alias
                break
    return mapping


# -------------------------------------------------------------- animation
def _q(axis, deg):
    axes = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}
    return Quaternion(axes[axis], math.radians(deg))


def _arm_rest_angle(rig, mapping, side):
    """Angle (deg) of the upper arm below horizontal in rest pose."""
    name = mapping.get("upper_arm." + side)
    if not name:
        return 0.0
    b = rig.data.bones[name]
    d = (b.tail_local - b.head_local).normalized()
    return math.degrees(math.asin(max(-1.0, min(1.0, -d.z))))


def procedural_pose(anim, p, arm_down):
    """Return ({bone: armature-space quaternion relative to parent}, root_loc, root_rot_x_deg).

    ``p`` is the animation phase 0..1. ``arm_down`` = degrees needed to bring
    the arms from rest pose down to the sides.
    """
    s = math.sin(2 * math.pi * p)
    c = math.cos(2 * math.pi * p)
    R = {}
    loc = [0.0, 0.0, 0.0]
    fall = 0.0

    def arms(swing_l=0.0, swing_r=0.0, bend_l=10.0, bend_r=10.0, raise_l=0.0, raise_r=0.0):
        # Lower arms to the sides (rotation about Y), then swing forward/back (about X).
        R["upper_arm.L"] = _q("X", -swing_l) @ _q("Y", arm_down - raise_l)
        R["upper_arm.R"] = _q("X", -swing_r) @ _q("Y", -(arm_down - raise_r))
        R["forearm.L"] = _q("Z", -bend_l)
        R["forearm.R"] = _q("Z", bend_r)

    def legs(l=0.0, r=0.0, knee_l=0.0, knee_r=0.0):
        R["thigh.L"] = _q("X", -l)
        R["thigh.R"] = _q("X", -r)
        R["shin.L"] = _q("X", knee_l)
        R["shin.R"] = _q("X", knee_r)

    if anim == "idle":
        arms(swing_l=2 * s, swing_r=-2 * s, bend_l=8, bend_r=8)
        R["chest"] = _q("X", 1.5 * s)
        R["head"] = _q("X", -1.0 * s)
        loc[2] = -0.006 * (1 - c)
    elif anim in ("walk", "run"):
        amp = 28.0 if anim == "walk" else 42.0
        lean = 4.0 if anim == "walk" else 12.0
        legs(amp * s, -amp * s, knee_l=max(0.0, 35 * -c) + 5, knee_r=max(0.0, 35 * c) + 5)
        arms(swing_l=-amp * 0.8 * s, swing_r=amp * 0.8 * s, bend_l=15 if anim == "walk" else 60, bend_r=15 if anim == "walk" else 60)
        R["spine"] = _q("X", lean)
        R["chest"] = _q("Z", 4 * s)
        loc[2] = -0.02 * abs(c) * (1.0 if anim == "walk" else 2.0)
    elif anim in ("attack", "heavy_attack"):
        heavy = anim == "heavy_attack"
        # wind-up (0..0.4) -> strike (0.4..0.6) -> recover
        if p < 0.4:
            k = p / 0.4
            swing, body = -(110 if heavy else 80) * k, -10 * k
        elif p < 0.6:
            k = (p - 0.4) / 0.2
            swing, body = -(110 if heavy else 80) + (170 if heavy else 140) * k, -10 + 25 * k
        else:
            k = (p - 0.6) / 0.4
            swing, body = (60 if heavy else 60) * (1 - k), 15 * (1 - k)
        arms(swing_l=swing * (1.0 if heavy else 0.2), swing_r=swing, bend_l=20, bend_r=25, raise_l=0, raise_r=0)
        R["spine"] = _q("X", body * 0.6) @ _q("Z", -body * (0.2 if heavy else 0.8))
        legs(-15, 15, 10, 10)
        loc[2] = -0.03 if heavy and 0.4 < p < 0.7 else 0.0
    elif anim == "hit":
        k = math.sin(math.pi * min(1.0, p * 1.4))
        R["spine"] = _q("X", -18 * k)
        R["head"] = _q("X", -12 * k)
        arms(swing_l=-20 * k, swing_r=-20 * k, bend_l=30, bend_r=30, raise_l=15 * k, raise_r=15 * k)
        loc[1] = 0.05 * k
    elif anim == "death":
        k = min(1.0, p * 1.2)
        fall = -88.0 * (k * k)
        R["spine"] = _q("X", -10 * k)
        R["head"] = _q("X", -20 * k)
        arms(swing_l=-40 * k, swing_r=-30 * k, bend_l=20, bend_r=20, raise_l=40 * k, raise_r=35 * k)
        legs(10 * k, -5 * k, 15 * k, 5 * k)
        loc[2] = -0.02 * k
    elif anim == "block":
        k = min(1.0, p * 2.0)
        arms(swing_l=70 * k, swing_r=60 * k, bend_l=80 * k, bend_r=90 * k)
        R["spine"] = _q("X", 6 * k)
        legs(-10 * k, 12 * k, 15 * k, 10 * k)
        loc[2] = -0.03 * k
    elif anim in ("skill", "cast"):
        k = math.sin(math.pi * p)
        if anim == "cast":
            arms(swing_l=90 * k, swing_r=90 * k, bend_l=10, bend_r=10, raise_l=20 * k, raise_r=20 * k)
            R["chest"] = _q("X", -8 * k)
        else:
            arms(swing_l=20 * k, swing_r=150 * k, bend_l=20, bend_r=10)
            R["spine"] = _q("Z", -15 * k)
        R["head"] = _q("X", -6 * k)
    elif anim == "dodge":
        k = math.sin(math.pi * p)
        loc[0] = -0.2 * k
        loc[2] = -0.1 * k
        R["spine"] = _q("X", 25 * k) @ _q("Y", -10 * k)
        legs(-40 * k, 30 * k, 60 * k, 70 * k)
        arms(swing_l=30 * k, swing_r=-20 * k, bend_l=40, bend_r=40)
    else:
        arms()
    return R, loc, fall


def apply_pose(rig, mapping, rotations):
    """Apply armature-space (relative-to-parent) rotations to pose bones."""
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion()
        pb.location = Vector((0, 0, 0))
    for canonical, rot in rotations.items():
        name = mapping.get(canonical)
        if not name:
            continue
        pb = rig.pose.bones[name]
        m = pb.bone.matrix_local.to_quaternion()
        pb.rotation_quaternion = m.inverted() @ rot @ m


def action_for(rig, anim):
    """An imported action matching the animation name, if any."""
    want = anim.lower().replace("_", "")
    for act in bpy.data.actions:
        if want in act.name.lower().replace("_", "").replace(" ", ""):
            return act
    return None


def set_frame_pose(ctx, anim, frame_index, frame_count, loop):
    """Pose the character for one frame. Returns the method used."""
    rig = ctx.get("rig")
    root = ctx["root"]
    holder = ctx["holder"]
    p = frame_index / frame_count if loop else (frame_index / max(1, frame_count - 1))
    set_holder_offset(holder, (0.0, 0.0, 0.0), 0.0)
    if rig is None:
        return "static"
    if anim in (None, "", "static"):
        if rig.animation_data:
            rig.animation_data.action = None
        apply_pose(rig, ctx["mapping"], {})
        return "static"
    act = action_for(rig, anim) if ctx.get("use_actions", True) else None
    if act is not None:
        if rig.animation_data is None:
            rig.animation_data_create()
        rig.animation_data.action = act
        start, end = act.frame_range
        frame = start + (end - start) * p
        bpy.context.scene.frame_set(int(math.floor(frame)), subframe=frame - math.floor(frame))
        return "action:" + act.name
    if rig.animation_data:
        rig.animation_data.action = None
    rotations, loc, fall = procedural_pose(anim, p, ctx["arm_down"])
    apply_pose(rig, ctx["mapping"], rotations)
    H = ctx["height"]
    set_holder_offset(holder, (loc[0] * H, loc[1] * H, loc[2] * H), fall)
    return "procedural"


def set_holder_offset(holder, offset, fall_deg):
    """Animate the character holder relative to its stored base transform."""
    base = holder.get("studio_base_matrix")
    base_m = Matrix([base[0:4], base[4:8], base[8:12], base[12:16]]) if base else Matrix.Identity(4)
    fall = Matrix.Rotation(math.radians(fall_deg), 4, "X")
    holder.matrix_basis = Matrix.Translation(Vector(offset)) @ fall @ base_m


# --------------------------------------------------------------- camera
def setup_camera(cfg, target):
    cam_data = bpy.data.cameras.get(CAMERA_NAME) or bpy.data.cameras.new(CAMERA_NAME)
    cam = bpy.data.objects.get(CAMERA_NAME)
    if cam is None:
        cam = bpy.data.objects.new(CAMERA_NAME, cam_data)
        bpy.context.scene.collection.objects.link(cam)
    cam_data.type = "ORTHO" if cfg.get("projection", "ortho") == "ortho" else "PERSP"
    elev = math.radians(float(cfg.get("elevation_deg", 30.0)))
    yaw = math.radians(float(cfg.get("yaw_offset_deg", 0.0)))
    dist = 20.0
    offset = Vector((0, -math.cos(elev) * dist, math.sin(elev) * dist))
    offset.rotate(Quaternion(Vector((0, 0, 1)), yaw))
    cam.location = Vector(target) + offset
    direction = Vector(target) - cam.location
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = direction.to_track_quat("-Z", "Y")
    cam_data.clip_start = 0.1
    cam_data.clip_end = 100.0
    bpy.context.scene.camera = cam
    bpy.context.view_layer.update()
    return cam


def compute_framing(cam, points, target, width, height, margin):
    """Smallest orthographic framing containing all points (plus margin).

    Returns (ortho_scale, new_target): the camera is re-centred on the
    projected bounding box so no canvas space is wasted.
    """
    m = cam.matrix_world
    right = np.array((m[0][0], m[1][0], m[2][0]))
    up = np.array((m[0][1], m[1][1], m[2][1]))
    rel = points - np.array(target)
    xs, ys = rel @ right, rel @ up
    cx, cy = (xs.max() + xs.min()) / 2, (ys.max() + ys.min()) / 2
    hx = (xs.max() - xs.min()) / 2 * margin
    hy = (ys.max() - ys.min()) / 2 * margin
    aspect = width / float(height)
    ortho = max(2 * hx, 2 * hy * aspect) if width >= height else max(2 * hy, 2 * hx / aspect)
    new_target = np.array(target) + right * cx + up * cy
    return float(ortho), [float(v) for v in new_target]


def rotate_points_z(points, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    rot = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return points @ rot.T


def framing_points(ctx, animations):
    """Mesh vertices over sampled poses of all animations (subsampled)."""
    mesh = ctx["mesh"]
    pts = []
    # With a rig, the rest (T-)pose is never shown, so it must not influence framing.
    samples = [] if (ctx.get("rig") is not None and animations) else [("static", 0, 1, True)]
    for a in animations:
        n = 4
        for i in range(n):
            samples.append((a, i, n, False))
    for anim, i, n, loop in samples:
        set_frame_pose(ctx, anim, i, n, loop)
        bpy.context.view_layer.update()
        co = world_vertices(mesh)
        step = max(1, len(co) // 3000)
        pts.append(co[::step])
    set_frame_pose(ctx, "static", 0, 1, True)
    allp = np.concatenate(pts)
    # all 8 directions (model yaw)
    return np.concatenate([rotate_points_z(allp, -d) for d in range(0, 360, 45)])


# ---------------------------------------------------------------- render
def setup_render(cfg):
    scene = bpy.context.scene
    r = cfg.get("render", {})
    engine = r.get("engine", "cycles").lower()
    engines = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    if engine == "eevee":
        chosen = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    else:
        chosen = "CYCLES"
    scene.render.engine = chosen
    if chosen == "CYCLES":
        scene.cycles.samples = int(r.get("samples", 8))
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 0
        scene.cycles.device = "GPU" if r.get("gpu") else "CPU"
        if hasattr(scene.cycles, "use_adaptive_sampling"):
            scene.cycles.use_adaptive_sampling = False
        scene.cycles.pixel_filter_type = "BOX"
        scene.cycles.filter_width = 1.0
    scene.render.resolution_x = int(r.get("width", 256))
    scene.render.resolution_y = int(r.get("height", 256))
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    if scene.world is None:
        scene.world = bpy.data.worlds.new("StudioWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0, 0, 0, 1)
        bg.inputs[1].default_value = 0.0
    return chosen


# ------------------------------------------------------------------ modes
def setup_hierarchy(mesh_obj, rig):
    """StudioRoot (direction yaw) -> holder/rig (animation offsets) -> mesh."""
    root = bpy.data.objects.new(ROOT_NAME, None)
    bpy.context.scene.collection.objects.link(root)
    holder = rig
    if rig is None:
        holder = bpy.data.objects.new("StudioHolder", None)
        bpy.context.scene.collection.objects.link(holder)
        mesh_obj.parent = holder
    # Every other top-level object (e.g. an imported rig's meshes) follows the holder.
    for o in list(bpy.context.scene.objects):
        if o.parent is None and o not in (root, holder) and o.type in ("MESH", "ARMATURE", "EMPTY"):
            mw = o.matrix_world.copy()
            o.parent = holder
            o.matrix_parent_inverse = holder.matrix_world.inverted()
            o.matrix_world = mw
    holder.parent = root
    holder["studio_base_matrix"] = [v for row in holder.matrix_basis for v in row]
    return root, holder


def run_prepare(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imported = import_model(job["model_path"])
    cfg = job.get("cleanup", {})
    existing_rig = next((o for o in imported if o.type == "ARMATURE"), None)
    rig_cfg = job.get("rig", {"mode": "none"})
    rig_mode = rig_cfg.get("mode", "none")
    result = {}
    if existing_rig is not None and rig_mode in ("auto_humanoid", "existing"):
        info("Using the rig contained in the imported model")
        mesh_obj = next(o for o in imported if o.type == "MESH")
        rig = existing_rig
        # Normalise via the rig object (keeps skinning intact)
        co = np.concatenate([world_vertices(o) for o in imported if o.type == "MESH"])
        H = float(co[:, 2].max() - co[:, 2].min())
        s = float(cfg.get("target_size", 2.0)) / max(1e-9, H)
        top = [o for o in imported if o.parent is None]
        for o in top:
            o.scale = (o.scale[0] * s, o.scale[1] * s, o.scale[2] * s)
        bpy.context.view_layer.update()
        co = np.concatenate([world_vertices(o) for o in imported if o.type == "MESH"])
        mn, mx = co.min(axis=0), co.max(axis=0)
        for o in top:
            o.location = (o.location[0] - (mn[0] + mx[0]) / 2, o.location[1] - (mn[1] + mx[1]) / 2, o.location[2] - mn[2])
        bpy.context.view_layer.update()
        result["rig"] = {"mode": "existing", "bones": len(rig.data.bones), "actions": [a.name for a in bpy.data.actions]}
        mapping = bone_map(rig)
        result["rig"]["mapped_bones"] = len(mapping)
        if len(mapping) < 10:
            warn("The imported rig uses unknown bone names (%d of %d mapped); procedural animations may not move it. "
                 "Animations contained in the file are used when their names match." % (len(mapping), len(HUMANOID_BONES)))
        light_dir = light_vector(*job.get("light", {}).get("direction", (-45.0, 50.0)))
        for m in [o for o in imported if o.type == "MESH"]:
            apply_materials(m, dict(job, texture={"mode": "keep"}), light_dir)
        mesh_obj.name = MODEL_NAME
        result["mesh"] = {"dimensions": [float(v) for v in (mx - mn)]}
    else:
        if existing_rig is not None:
            warn("The model contains an armature but rigging is disabled; the armature is ignored.")
            for o in imported:
                if o.type == "ARMATURE":
                    bpy.data.objects.remove(o)
        mesh_obj = join_meshes([o for o in imported if o.type == "MESH" and o.name in bpy.data.objects])
        apply_transforms(mesh_obj)  # bake parent transforms before removing helper objects
        for o in list(bpy.data.objects):
            if o != mesh_obj:
                bpy.data.objects.remove(o)
        mesh_obj.name = MODEL_NAME
        stats = cleanup_mesh(mesh_obj, cfg)
        stats.update(normalize(mesh_obj, cfg))
        result["mesh"] = stats
        light_dir = light_vector(*job.get("light", {}).get("direction", (-45.0, 50.0)))
        result["texture_mode"] = apply_materials(mesh_obj, job, light_dir)
        rig = None
        if rig_mode == "auto_humanoid":
            rig, rig_info = build_humanoid_rig(mesh_obj)
            rig_info.update(skin_mesh(mesh_obj, rig))
            rig_info["mode"] = "auto_humanoid"
            rig_info["bones"] = len(rig.data.bones)
            result["rig"] = rig_info
        else:
            result["rig"] = {"mode": "none"}
    root, holder = setup_hierarchy(mesh_obj, rig)
    scene = bpy.context.scene
    H = float(world_vertices(mesh_obj)[:, 2].max())
    scene["studio_height"] = H
    scene["studio_rig_mode"] = result["rig"]["mode"]
    result["height"] = H

    # Export a cleaned model (static GLB) and save the editable .blend
    if job.get("export_model_path"):
        select_only([mesh_obj])
        try:
            bpy.ops.export_scene.gltf(filepath=job["export_model_path"], export_format="GLB", use_selection=True)
            result["exported_model"] = job["export_model_path"]
        except Exception as exc:  # noqa: BLE001
            warn("Exporting the cleaned GLB failed: %s" % exc)
    REPORT.update(result)
    if job.get("blend_path"):
        bpy.ops.wm.save_as_mainfile(filepath=job["blend_path"])
        REPORT["blend_path"] = job["blend_path"]


def run_render(job):
    bpy.ops.wm.open_mainfile(filepath=job["blend_path"])
    scene = bpy.context.scene
    mesh = bpy.data.objects.get(MODEL_NAME)
    root = bpy.data.objects.get(ROOT_NAME)
    if mesh is None or root is None:
        raise RuntimeError("The .blend file was not prepared by Pixel RPG Asset Studio (missing %s/%s)." % (MODEL_NAME, ROOT_NAME))
    rig = find_armature()
    holder = rig if rig is not None else bpy.data.objects.get("StudioHolder")
    H = float(scene.get("studio_height", 2.0))
    mapping = bone_map(rig) if rig else {}
    arm_down = 0.0
    if rig:
        rest = (_arm_rest_angle(rig, mapping, "L") + _arm_rest_angle(rig, mapping, "R")) / 2
        arm_down = max(0.0, 78.0 - rest)
    ctx = {"rig": rig, "root": root, "holder": holder, "mesh": mesh, "height": H, "mapping": mapping,
           "arm_down": arm_down, "use_actions": job.get("use_actions", True)}

    # Re-apply light direction (style may have changed since prepare). The
    # light is rotated with the camera's perspective yaw so it stays fixed on screen.
    if job.get("light"):
        ld = light_vector(*job["light"]["direction"])
        ld.rotate(Quaternion(Vector((0, 0, 1)), math.radians(float(job.get("camera", {}).get("yaw_offset_deg", 0.0)))))
        for mat in bpy.data.materials:
            if mat.use_nodes:
                for n in mat.node_tree.nodes:
                    if n.type == "COMBXYZ":
                        n.inputs[0].default_value, n.inputs[1].default_value, n.inputs[2].default_value = ld
    engine = setup_render(job)
    REPORT["engine"] = engine
    cam_cfg = job.get("camera", {})
    target = tuple(cam_cfg["target"]) if cam_cfg.get("target") else (0.0, 0.0, 0.5 * H)
    cam = setup_camera(cam_cfg, target)
    r = job.get("render", {})
    width, height = int(r.get("width", 256)), int(r.get("height", 256))
    animations = [a["name"] for a in job.get("animations", []) if a.get("name") not in (None, "", "static")]
    if cam_cfg.get("ortho_scale") and cam_cfg.get("target"):
        ortho = float(cam_cfg["ortho_scale"])
    else:
        # Stable framing: computed once over ALL animations/directions of the
        # character, stored by the app and reused for every later render.
        frame_anims = job.get("framing_animations") or animations
        pts = framing_points(ctx, frame_anims)
        ortho, target = compute_framing(cam, pts, target, width, height, float(cam_cfg.get("margin", 1.06)))
        cam = setup_camera(cam_cfg, target)
    cam.data.ortho_scale = ortho
    REPORT["ortho_scale"] = ortho
    REPORT["target"] = list(target)
    out_dir = job["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    only = job.get("only_frames")  # optional [{"animation":..,"direction":..,"frame":..}]
    only_set = {(o["animation"], o["direction"], int(o["frame"])) for o in only} if only else None
    anim_specs = job.get("animations") or [{"name": "static", "frames": 1, "loop": True}]
    for spec in anim_specs:
        name = spec.get("name") or "static"
        count = max(1, int(spec.get("frames", 1)))
        loop = bool(spec.get("loop", True))
        for d in job.get("directions", [{"key": "s", "yaw_deg": 0.0}]):
            for i in range(count):
                if only_set is not None and (name, d["key"], i) not in only_set:
                    continue
                method = set_frame_pose(ctx, name, i, count, loop)
                root.rotation_euler = (0, 0, math.radians(-float(d["yaw_deg"])))
                bpy.context.view_layer.update()
                path = os.path.join(out_dir, name, d["key"], "frame_%03d.png" % i)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                REPORT["frames"].append({"animation": name, "direction": d["key"], "frame": i, "path": path, "method": method})
                if job.get("keyframes") and rig is not None and method == "procedural":
                    for pb in rig.pose.bones:
                        pb.keyframe_insert("rotation_quaternion", frame=i + 1)
    if job.get("save_blend"):
        bpy.ops.wm.save_mainfile()


def main():
    argv = sys.argv
    if "--" not in argv:
        print("usage: blender --background --python studio_blender.py -- job.json")
        sys.exit(2)
    job_path = argv[argv.index("--") + 1]
    with open(job_path, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    report_path = job.get("report_path") or os.path.splitext(job_path)[0] + "_report.json"
    start = time.time()
    code = 0
    try:
        if np is None:
            raise RuntimeError("numpy is not available in this Blender build.")
        mode = job.get("mode")
        if mode == "prepare":
            run_prepare(job)
        elif mode == "render":
            run_render(job)
        else:
            raise RuntimeError("Unknown job mode %r" % mode)
        REPORT["ok"] = True
    except Exception as exc:  # noqa: BLE001 - everything goes into the report
        traceback.print_exc()
        REPORT["errors"].append("%s: %s" % (type(exc).__name__, exc))
        REPORT["traceback"] = traceback.format_exc()
        code = 1
    REPORT["duration_s"] = round(time.time() - start, 2)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, indent=2)
    sys.stdout.flush()
    # Exit explicitly so Blender does not stay open if run without --background.
    os._exit(code)


if __name__ == "__main__":
    main()
