# User guide

## 1. Install the free tools

| Tool | Why | Where |
|---|---|---|
| **ComfyUI** (Portable *or* Desktop) | runs the AI models on your GPU | Portable: <https://github.com/comfyanonymous/ComfyUI/releases> (download `ComfyUI_windows_portable_nvidia.7z` and extract it, e.g. to `D:\AI\`). Desktop: <https://www.comfy.org/download> |
| **Blender** 3.6 LTS or newer | cleans up 3D models, rigs, animates and renders them | <https://www.blender.org/download/> |
| **NVIDIA driver** | GPU access | <https://www.nvidia.com/Download/index.aspx> |

You never have to open ComfyUI or Blender yourself. The studio starts and controls them.

## 2. Install the AI models (once)

Open **AI Models**. Each required model is listed with:

- its status (installed / missing)
- the folder it belongs in (e.g. `ComfyUI\models\checkpoints`)
- the official download page and its license

Download each model and place it in the folder shown. Then press *Refresh*. If you prefer another model (any SDXL checkpoint, another pixel-art LoRA), put it in the same folder and use **Choose Installed File…**.

> Read the licenses. The default 3D model (Hunyuan3D-2mini) is **not licensed for use in the EU, UK and South Korea**. Outside those territories, check its other conditions. You can always **import 3D models** instead (made in Blender, bought, or from another tool), and the rest of the pipeline works exactly the same.

## 3. First start

Double-click `PixelRPGAssetStudio.exe`. The setup wizard:

1. finds ComfyUI (or lets you *Choose Folder*: for Portable, pick the folder with `run_nvidia_gpu.bat`; for Desktop, pick `ComfyUI.exe`)
2. finds Blender (or lets you choose `blender.exe`)
3. shows a status table (GPU, VRAM, ComfyUI, Blender, Image AI, 3D AI, pixel tools, storage)

If ComfyUI is installed, the app starts it in the background on every launch (*Settings → Start ComfyUI automatically*). The status bar always shows whether the local AI is connected, plus GPU memory, the job queue and progress.

## 4. Create a project and define the style

**Projects → Create Project** creates one folder per game:

```
MyGame/  project.json  style/  characters/  weapons/  items/  props/  environment/
         buildings/  tiles/  tilesets/  backgrounds/  vfx/  spritesheets/  exports/
```

On the **Style** page, set the things every asset must share:

- sprite size, tile size, render oversampling
- perspective (side, ¾ top-down, top-down, isometric) and camera angle
- light direction, shading style and number of shading bands
- outline style, contrast, saturation, colour count and palette (type hex colours, import `.hex`/`.gpl`, or *Extract from References*)
- prompt fragments added to every generation
- style reference images, and `rules.md` for your own notes

## 5. Characters

1. **New** → name the character, then describe it (description, equipment, weapon, colours).
2. Optionally add **reference images**. With *Use first reference* checked, the first one guides the concept.
3. Choose the target resolution, directions (1, side, 4 or 8) and animations, with frames, FPS and loop for each.
4. Press **▶ Run Full Pipeline**, or run the steps one at a time:
   - *Generate Concept* (or *Import Concept Image…*) produces the master reference
   - *Generate 3D Model* (or *Import 3D Model…*: GLB, FBX, OBJ...)
   - *Prepare Model* – cleanup, colours and automatic rig. Read the **rig report** under the buttons.
   - *Render Animations* – renders, pixel-processes and builds the sprite sheet
5. Review in the **Animation** tab (play/pause, FPS, step through frames, *Show raw render*). The text under the preview compares the selected frame with the master reference (palette match, proportions).
6. **Regenerate Selected Frame** re-renders just that frame with identical settings. **Re-render This Animation** redoes one animation.
7. If the model faces away from the camera, set *Model facing → Rotate 180°*, then run *Prepare Model* again.
8. Rig not good enough? **Open .blend in Blender**, fix bones or weights (Weight Paint), save, then *Render Animations* again.

## 6. Weapons, items, props, environment, buildings

Describe the object, pick the **views** (front, sides, back, diagonals) and press **Run Full Pipeline**. Uncheck *Use 3D* for a direct concept → pixel sprite. Buildings also have a footprint (in tiles), perspective/camera-angle overrides including true top-down, and a materials field. The **Compare** button shows all views side by side.

## 7. Tiles and tilesets

1. Choose the base terrain (e.g. grass) and the overlay terrain (e.g. dirt).
2. **Generate Complete Tileset** creates two seamless tiles, 16 corner transitions and random variants.
3. **Seamless Test** repeats the tile 4×4. *Show tile borders* toggles grid lines. The seam score tells you whether the wrap edge is stronger than normal texture detail.
4. **Connection Preview** shows a small map built from the transitions.
5. Export to Godot: you get a `TileSet` `.tres` with a *Match Corners* terrain set, ready for the TileMap terrain painting tool.

## 8. Backgrounds, VFX, sprite sheets

- **Backgrounds**: describe the scene and set the pixel resolution (e.g. 480×270). Optionally enable parallax layers. *Import Image…* pixelates your own art.
- **VFX**: pick a preset, frame count, canvas size, loop, FPS, direction and intensity. Results are deterministic per seed. *Save GIF Preview* writes an animated preview.
- **Sprite Sheets**: add or slice frames, then set frame size, columns, padding and FPS. *Build Sheet* writes the PNG and JSON metadata.

## 9. Preview and review buttons

Every studio has preview tabs: **Original** (your input), **AI Generated**, **Processed** and **Final**. *History / Metadata* shows every step with seed, model, workflow and prompt.

- **Accept** marks the result as final.
- **Edit** opens the image in your default image editor (e.g. Aseprite). Save it there, then press Accept.
- **Compare** shows master/AI/processed/final (or all views) side by side.
- **Export to Godot** exports into the Godot project.

## 10. Export to Godot

Set your Godot project folder once (*Projects → Godot project*, or on the first export). Exported files go to `res://assets/generated/<type>/<asset>/`:

- `<asset>_sheet.png` + `.json` and `<asset>_frames.tres` (a `SpriteFrames` resource with animations named `walk_s`, `idle_e`, ...)
- individual PNGs per view, `TileSet` `.tres` for tilesets, layer PNGs for backgrounds
- `scripts/pixel_rpg_directional_sprite.gd`: attach it to an `AnimatedSprite2D` and call `play_action("walk")` and `face_vector(velocity)`

Existing files are never overwritten without asking.

**Projects → Export All Accepted Assets to Godot** exports every accepted asset in one go. **Game-ready Export to Project Folder** writes the same files to `<project>/exports/godot/`, ready to copy into any Godot project. For crisp pixels, set *Project Settings → Rendering → Textures → Default Texture Filter* to **Nearest** in Godot (the helper script also sets it per sprite).

## Low VRAM

*Settings → VRAM profile* is `auto` by default: ≤ 8 GB selects **low** (smaller images, ComfyUI `--lowvram`, models unloaded after each stage). An RTX 3060 12 GB uses **medium**. Stages always run one after another.
