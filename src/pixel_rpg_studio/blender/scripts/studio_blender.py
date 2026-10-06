"""Pixel RPG Asset Studio - Blender worker script.

Runs INSIDE Blender (``blender --background --factory-startup --python
studio_blender.py -- job.json``). It is generated/driven by the desktop app;
users never need to open Blender for normal operation.

Modes
-----
``prepare``  import a model (weapon, item, prop, building...), clean it up,
             normalise scale/orientation, project the concept image as
             colours, save a .blend and a cleaned GLB. The .blend can be
             opened and corrected manually - later renders use that file.
``render``   open the prepared .blend and render one view per requested
             direction with a fixed orthographic camera and deterministic
             "toon" shading (works with Cycles and EEVEE).

Characters do not use Blender (they are generated in 2D from a reference
image and OpenPose skeletons).

Every run writes a JSON report (``report_path``) with warnings and results.
Compatible with Blender 3.6 LTS - 5.x (tested with 4.2 LTS and 5.2 LTS).
"""

import json
import math
import os
import sys
import time
import traceback

import bmesh
import bpy
from mathutils import Quaternion, Vector

try:
    import numpy as np
except ImportError:  # pragma: no cover - Blender always bundles numpy
    np = None

REPORT = {"ok": False, "warnings": [], "errors": [], "frames": [], "blender_version": bpy.app.version_string}

ROOT_NAME = "StudioRoot"
MODEL_NAME = "StudioModel"
CAMERA_NAME = "StudioCamera"


def warn(msg):
    print("[studio] WARNING:", msg)
    REPORT["warnings"].append(msg)


def info(msg):
    print("[studio]", msg)


# ----------------------------------------------------------------- utilities
def enable_nodes(idblock):
    """Blender < 5 needs use_nodes = True; from 5.0 nodes are always on and the
    property is deprecated (removed in 6.0)."""
    if bpy.app.version < (5, 0, 0):
        idblock.use_nodes = True

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
    enable_nodes(mat)
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
            if src and src.node_tree is not None:
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


def framing_points(mesh):
    """Mesh vertices (subsampled), rotated to all 8 directions, so the framing
    is identical for every view."""
    co = world_vertices(mesh)
    pts = co[::max(1, len(co) // 3000)]
    return np.concatenate([rotate_points_z(pts, -d) for d in range(0, 360, 45)])


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
    enable_nodes(scene.world)
    bg = scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0, 0, 0, 1)
        bg.inputs[1].default_value = 0.0
    return chosen


# ------------------------------------------------------------------ modes
def setup_hierarchy(mesh_obj):
    """StudioRoot (direction yaw) -> mesh."""
    root = bpy.data.objects.new(ROOT_NAME, None)
    bpy.context.scene.collection.objects.link(root)
    mesh_obj.parent = root
    return root


def run_prepare(job):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imported = import_model(job["model_path"])
    cfg = job.get("cleanup", {})
    result = {}
    meshes = [o for o in imported if o.type == "MESH"]
    if any(o.type == "ARMATURE" for o in imported):
        warn("The model contains an armature; it is ignored (objects are rendered static).")
    mesh_obj = join_meshes(meshes)
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
    setup_hierarchy(mesh_obj)
    scene = bpy.context.scene
    H = float(world_vertices(mesh_obj)[:, 2].max())
    scene["studio_height"] = H
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
    H = float(scene.get("studio_height", 2.0))
    # Re-apply light direction (style may have changed since prepare). The
    # light is rotated with the camera's perspective yaw so it stays fixed on screen.
    if job.get("light"):
        ld = light_vector(*job["light"]["direction"])
        ld.rotate(Quaternion(Vector((0, 0, 1)), math.radians(float(job.get("camera", {}).get("yaw_offset_deg", 0.0)))))
        for mat in bpy.data.materials:
            if mat.node_tree is not None:
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
    if cam_cfg.get("ortho_scale") and cam_cfg.get("target"):
        ortho = float(cam_cfg["ortho_scale"])
    else:
        # Stable framing: computed once over all directions, stored by the app
        # and reused for every later render.
        pts = framing_points(mesh)
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
        for d in job.get("directions", [{"key": "s", "yaw_deg": 0.0}]):
            for i in range(count):
                if only_set is not None and (name, d["key"], i) not in only_set:
                    continue
                root.rotation_euler = (0, 0, math.radians(-float(d["yaw_deg"])))
                bpy.context.view_layer.update()
                path = os.path.join(out_dir, name, d["key"], "frame_%03d.png" % i)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                REPORT["frames"].append({"animation": name, "direction": d["key"], "frame": i, "path": path})
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
