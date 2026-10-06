# Changelog

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
