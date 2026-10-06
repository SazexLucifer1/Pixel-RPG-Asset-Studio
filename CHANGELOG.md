# Changelog

## Unreleased – characters rebuilt as a 2D reference pipeline

- **Breaking:** the 3D character pipeline (concept → Hunyuan3D → colour projection → auto rig → procedural animation → render) and its UI, settings and workflows (`character_concept*`) were removed. Blender/Hunyuan stay for weapons, items, props, environment and buildings. Old character assets are not converted.
- New Character Studio: Reference Image → Create Character (identity, palette locked from the reference) → animation (Idle, Walk, Run, Attack, Hurt, Death, Custom; adjustable frames) × Front/Back/Left/Right → Generate Animation → frames → Export PNG / Sprite Sheet / Godot
- New `character_frame` workflow: SDXL + pixel LoRA + IP-Adapter Plus (reference image conditioning, "Reference Strength") + OpenPose ControlNet ("Pose Strength"); one frame per request, 768 px canvas on 8 GB GPUs
- Pose system: skeleton with forward kinematics and per-direction projection (8 directions ready), presets (Idle, Walk 1–4, Run 1–4, Attack 1–3, Hurt, Death…), pose editor with draggable joints, user presets
- Regenerate Selected Frame changes only the seed; every frame records seed, prompts, strengths, model, workflow, resolution, palette, direction, animation and frame index
- Sprite sheets per animation (`knight_walk.png` + `.json`) exported to Godot together with the SpriteFrames resource
- AI Models / Diagnostics list IP-Adapter, CLIP Vision ViT-H, xinsir OpenPose ControlNet and explain how to install ComfyUI_IPAdapter_plus

## 0.1.0 – first development release

- Application shell (PySide6) with Dashboard, Projects, all asset studios, Style, AI Models, Settings and Diagnostics
- First-run setup wizard; ComfyUI detection (Portable / Desktop / manual) and auto-start; Blender detection; GPU/VRAM detection with Low VRAM profile
- Project system with global style, palette, references, per-asset metadata and full generation history
- ComfyUI layer: HTTP client, WebSocket progress, workflow templates with manifests, server-side validation, explained errors
- Character vertical slice: concept → master reference → Hunyuan3D (ComfyUI) or imported model → Blender cleanup, colour projection, automatic rig → 11 procedural animations × 1/2/4/8 directions → deterministic pixel processing → sprite sheets → Godot SpriteFrames
- Single-frame regeneration with identical camera framing and settings
- Weapons/items/props/environment/buildings (multi-view), seamless tiles and Godot terrain tilesets, backgrounds with parallax layers, procedural VFX, sprite sheet builder
- Godot export with conflict protection and helper script
- 134 automated tests (fake ComfyUI server, real Blender, offscreen UI); Windows build script and GitHub Actions
