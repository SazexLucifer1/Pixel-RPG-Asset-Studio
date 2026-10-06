# CLAUDE.md – notes for AI-assisted maintenance

Pixel RPG Asset Studio: PySide6 desktop app (Windows-first) that drives **local** ComfyUI and Blender to produce pixel-art assets for Godot. Claude/any paid API must never become a runtime dependency.

## Commands
- Run (demo, no AI): `PYTHONPATH=src python -m pixel_rpg_studio --mock`
- Tests: `QT_QPA_PLATFORM=offscreen python -m pytest -q` (real-Blender tests run when Blender is found; `PIXEL_RPG_TEST_BLENDER=/path/to/blender` forces one)
- Headless pipeline check: `python -m pixel_rpg_studio --self-test`
- Windows build: `scripts\build_windows.ps1` (PyInstaller spec in `build/`)

## Layout (src/pixel_rpg_studio)
core (config, paths, errors, jobs, logging) · project (project, asset, style, asset_types) · providers (interfaces in base.py, registry.py, comfyui/blender/mock implementations) · comfyui (client, workflows, launcher, ws) · blender (detect, runner, scripts/studio_blender.py runs INSIDE Blender; objects only) · imaging (pure deterministic image ops, openpose drawing) · poses (skeleton FK/projection, presets, animation templates) · pipeline (shared stages; character (2D) / three_d + objects / backgrounds / tiles / vfx / sheets) · spritesheet · export/godot.py · system (gpu, diagnostics) · models (catalog) · ui (pages, widgets incl. pose_editor, services, backend, main_window, setup_wizard)

## Rules
- Heavy work only via `Services.submit` / `JobQueue` (one worker thread, sequential GPU stages).
- User-facing failures: raise `StudioError(message, hint, details)`; never show bare exceptions.
- Every generation/processing step appends a `GenerationRecord` (seed, model, workflow, prompts, params, refs, outputs).
- No node IDs or model filenames in Python code: they belong in `workflows/*.json` + manifests and `config/models_catalog.json`.
- Image processing stays deterministic and lives in `imaging/` as pure functions.
- Pillow ≥ 12: always `.copy()` an image created with `Image.fromarray` before drawing on it in place (see `imaging.pixel.from_array`).
- `studio_blender.py` must stay compatible with Blender 3.6–5.x (tested: 4.2 LTS, 5.2 LTS) and use only bpy/bmesh/mathutils/numpy.
- Never commit model weights, generated assets, user projects or secrets.
- Characters have exactly ONE pipeline: reference → identity → OpenPose pose → `character_frame` workflow (IP-Adapter + ControlNet) → pixel processing (`pipeline/character.py`). Do not add a 3D/rig path or a hidden fallback for characters. Frames are generated one request at a time (8 GB GPUs).
- New asset type: register in `project/asset_types.py`, compose from `pipeline/common.py` + `pipeline/three_d.py`, add a page (often `ObjectStudioPage`).
- Docs to keep updated: README.md, docs/user_guide.md, docs/troubleshooting.md, docs/decisions.md.
