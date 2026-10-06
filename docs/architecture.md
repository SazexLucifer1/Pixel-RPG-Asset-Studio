# Architecture

Pixel RPG Asset Studio is a single Python process: a PySide6 desktop UI plus a background job queue. It drives two external local engines:

```
┌──────────────────────────── PixelRPGAssetStudio.exe ────────────────────────────┐
│  ui/  (PySide6 pages, widgets, setup wizard)                                    │
│     │ signals (queued to the GUI thread)                                        │
│  ui/services.py ── core/jobs.py (single worker thread, sequential stages)       │
│     │                                                                           │
│  pipeline/  common · three_d · character · objects · backgrounds · tiles · vfx  │
│     │            uses only interfaces ↓                                         │
│  providers/ base.py (ImageGeneration / ThreeDGeneration / Renderer /            │
│             Animation / ImageProcessing)  ← registry.py picks implementations   │
│     ├─ comfyui_providers ── comfyui/ client · workflows · launcher · ws         │──HTTP/WS──► ComfyUI (local)
│     ├─ blender_provider ─── blender/ runner · detect · scripts/studio_blender.py│──subprocess──► Blender (headless)
│     └─ mock (tests / demo mode)                                                 │
│  imaging/  pixel · tiles · vfx · compare      (deterministic, numpy + Pillow)   │
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

### Character vertical slice (`pipeline/character.py`)

1. **Concept**: `generate_concept` builds the prompt from the style, asset type and description, calls `ImageGenerationProvider`, and stores the image plus a `GenerationRecord` (seed, model, workflow, prompts, params, reference hashes).
2. **Master reference**: the concept goes through the deterministic pixel pipeline into `master_reference.png`.
3. **3D model**: `threed_input_image` places the subject on a white square, then `ThreeDGenerationProvider` (Hunyuan3D via ComfyUI) returns a GLB. An imported model also works.
4. **Prepare** (Blender `prepare` mode):
   - join meshes, merge by distance, remove floating islands, recalculate normals, decimate
   - normalise scale and ground the feet
   - planar front projection of the concept colours
   - engine-independent toon material
   - automatic humanoid rig with heat weights (falls back to envelope weights)
   - save an editable `.blend` and a clean GLB
5. **Render** (Blender `render` mode): fixed orthographic camera at the style's elevation; light fixed in camera space. On the first render, the framing (ortho scale and target) is computed over all animations and directions, then stored and reused. That keeps feet on the same pixel row in every animation and every regenerated frame.
6. **Pixel processing**: mode-downscale of the whole frame (no per-frame re-centering, so frames stay aligned), locked palette, orphan cleanup, outline.
7. **Sprite sheet + metadata**, then **Godot export**.

Each stage can be re-run on its own. `regenerate_frame` re-renders exactly one frame from the same scene and framing.

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
