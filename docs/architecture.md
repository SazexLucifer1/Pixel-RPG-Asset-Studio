# Architecture

Pixel RPG Asset Studio is a single Python process: a PySide6 desktop UI plus a background job queue. It drives two external local engines:

```
┌──────────────────────────── PixelRPGAssetStudio.exe ────────────────────────────┐
│  ui/  (PySide6 pages, widgets, setup wizard)                                    │
│     │ signals (queued to the GUI thread)                                        │
│  ui/services.py ── core/jobs.py (single worker thread, sequential stages)       │
│     │                                                                           │
│  pipeline/  common · character · three_d · objects · backgrounds · tiles · vfx  │
│     │            uses only interfaces ↓                                         │
│  providers/ base.py (ImageGeneration / ThreeDGeneration / Renderer /            │
│             ImageProcessing)  ← registry.py picks implementations               │
│     ├─ comfyui_providers ── comfyui/ client · workflows · launcher · ws         │──HTTP/WS──► ComfyUI (local)
│     ├─ blender_provider ─── blender/ runner · detect · scripts/studio_blender.py│──subprocess──► Blender (headless)
│     └─ mock (tests / demo mode)                                                 │
│  poses/    skeleton · presets  (OpenPose poses for characters)                  │
│  imaging/  pixel · tiles · vfx · compare · openpose (deterministic)             │
│  spritesheet/builder · export/godot                                             │
│  project/  project · asset · style · asset_types   (JSON on disk)               │
│  core/     config · paths · errors · logging · jobs                             │
│  system/   gpu · diagnostics            models/ catalog                         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| UI | `ui/` | Pages and widgets only. Never runs heavy work on the GUI thread; submits jobs through `Services.submit`. |
| Asset/project management | `project/` | Project folders, `project.json`, global style (`style/style.json`, `palette.json`), asset folders with `asset.json` metadata and `GenerationRecord`s. |
| AI provider layer | `providers/` | Abstract interfaces plus local implementations. `registry.py` maps settings to implementations. Optional cloud providers would register here; nothing requires them. |
| ComfyUI integration | `comfyui/` | HTTP client (health, upload, queue, history, view, interrupt, free), optional WebSocket progress, workflow templates with manifests, install detection/launch. |
| Blender integration | `blender/` | Executable detection, headless runner, and the in-Blender worker script (`prepare` and `render` modes). |
| Image / pixel processing | `imaging/` | Pure deterministic functions: background removal, mode downscaling, palette quantisation, outlines, orphan cleanup, seamless tiles, terrain sets, VFX, consistency metrics. |
| Sprite sheets | `spritesheet/` | Layout, metadata, slicing, GIF previews. |
| Godot export | `export/` | Export plans with conflict detection, `SpriteFrames`/`TileSet` `.tres` writers, helper GDScript. |
| Configuration | `core/config.py` | Typed settings dataclasses stored in `%APPDATA%/PixelRPGAssetStudio/settings.json`. |
| Logging | `core/logging_setup.py` | Rotating `studio.log`. ComfyUI and Blender output go to `comfyui.log` / `blender.log`. |
| Diagnostics | `system/` | GPU/VRAM (nvidia-smi), hardware profiles, the full diagnostic used by the wizard, Diagnostics page and `--diagnose`. |

## Key flows

### Character pipeline (`pipeline/character.py`, `poses/`)

1. **Reference** (`set_reference`): stored as `reference/reference.png` with its hash.
2. **Identity** (`create_identity`): background removed, cropped and centred on a white 1024² square (`reference_clean.png`, the IP-Adapter input); pixel preview; palette extracted from the reference and locked; everything in `identity/identity.json` (sprite size, strengths, seed, AI canvas size from the VRAM profile – 768 px on ≤ 8 GB).
3. **Poses** (`poses/skeleton.py`, `poses/presets.py`): a small humanoid skeleton described by joint angles (`PoseParams`), forward kinematics, orthographic projection for any yaw (front/back/left/right now, diagonals ready) with OpenPose face-point visibility. Animation templates sample key presets (Idle, Walk 1–4, Attack 1–3, Hurt, Death…) for any frame count. Poses are materialised per frame in `animations/<anim>/<dir>/poses.json` and can be edited (pose editor) or replaced by presets; `imaging/openpose.py` draws the COCO-18 control image.
4. **Frame** (`generate_frame`): one `ImageRequest` with the `character_frame` workflow – SDXL + pixel LoRA, `IPAdapterAdvanced` (weight = Reference Strength, CLIP Vision ViT-H) and `ControlNetApplyAdvanced` with the OpenPose image (strength = Pose Strength). Output kept in `generated/`, copied to `raw/`, and a `GenerationRecord` per frame.
5. **Pixel processing** (`frame_steps`): background flood-fill on the full canvas, binary alpha, mode-downscale of the whole canvas (no per-frame cropping, so all frames share scale and ground line), quantise to the identity palette, orphan cleanup, outline.
6. **Sheets + Godot**: `export/<name>_<animation>.png/.json` (one row per direction) and a combined sheet for the `SpriteFrames` resource.

`regenerate_frame` repeats the last generation of that frame with its recorded prompt and strengths and only a new seed.

### 3D objects (`pipeline/three_d.py`)

Weapons, items, props, environment and buildings: concept → Hunyuan3D (or imported model) → Blender `prepare` (cleanup, scale, front colour projection, toon material, `.blend` + GLB) → Blender `render` (fixed orthographic camera; framing computed once over all directions and stored) → pixel processing per view.

### Workflow abstraction

The UI and pipelines never mention ComfyUI node IDs. A workflow is an API-format graph plus a manifest that maps parameter names (`prompt`, `seed`, `width`, `init_image`...) and model roles (`image.checkpoint`, `image.pixel_lora`, `threed.shape_model`) to node inputs. Model file names come from settings (AI Models page), so nothing is hard-wired to one model. Search order: `<project>/workflows/`, then `%APPDATA%/PixelRPGAssetStudio/workflows/`, then the bundled `workflows/`. See [workflows.md](workflows.md).

### Error handling

All failures become a `StudioError` with a plain-language `message`, an actionable `hint` and the technical `details`, which are never hidden. ComfyUI-specific translation covers missing models (`value_not_in_list`), missing nodes, execution errors (including CUDA OOM → Low VRAM advice) and HTTP 5xx. Jobs never raise into the UI. Failures arrive as `ErrorReport`s and are shown in the error dialog with *View Log*, *Copy Error* and *Run Diagnostic*.

### Threading

`core/jobs.JobQueue` has exactly one worker thread. That serialises GPU work, which keeps VRAM predictable and logs readable. Progress and cancellation go through `JobContext`. ComfyUI jobs are interrupted via `/interrupt`; Blender jobs are killed. The UI receives events through Qt signals, which are delivered on the GUI thread.

## Extending

- **New asset type**: register an `AssetType` in `project/asset_types.py`, compose a pipeline from `pipeline/common.py` and `pipeline/three_d.py`, and add a page (most types can reuse `ObjectStudioPage`).
- **New AI provider**: implement an interface from `providers/base.py` and call `register_image_provider` / `register_threed_provider` / `register_renderer_provider`. It then shows up in Settings.
- **New ComfyUI workflow**: export it from ComfyUI in API format and add a manifest. No code changes are needed.
