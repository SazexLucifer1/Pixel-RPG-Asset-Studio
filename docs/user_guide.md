# User guide

## 1. Install the free tools

| Tool | Why | Where |
|---|---|---|
| **ComfyUI** (Portable *or* Desktop) | runs the AI models on your GPU | Portable: <https://github.com/comfyanonymous/ComfyUI/releases> (download `ComfyUI_windows_portable_nvidia.7z` and extract it, e.g. to `D:\AI\`). Desktop: <https://www.comfy.org/download> |
| **Blender** 3.6 LTS or newer | cleans up and renders 3D models of weapons, items, props and buildings | <https://www.blender.org/download/> |
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

There is exactly one character workflow:
**Reference image → Character identity → Pose → AI generation → Pixel art → Animation frames → Sprite sheet → Godot**.

**Needed once in ComfyUI** (see the *AI Models* page for links and licenses):
- the custom node pack **ComfyUI_IPAdapter_plus** (ComfyUI → Manager → Custom Nodes Manager → search *IPAdapter plus* → Install → restart ComfyUI)
- `models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors`
- `models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`
- `models/controlnet/xinsir-openpose-sdxl-1.0.safetensors`
- plus the SDXL checkpoint and pixel-art LoRA you already use

**Steps**
1. **New** → name the character (e.g. *Knight*).
2. **CHARACTER → Reference Image**: choose your character (full body, plain background is best). Optional: a short *Description* that supports the image, the *Sprite size* (16, 32, **48**, 64, 128) and an *Equipment* reference (close-up of weapon/shield).
3. **Create Character** builds the identity: a cleaned copy of the reference (what the AI sees), a pixel preview and a palette locked from the reference (`identity/identity.json`).
4. **IDENTITY**: *Reference Strength* = how strongly the reference controls the result (IP-Adapter weight). *Pose Strength* = how strictly the skeleton is followed (ControlNet strength). Good start: 0.8 / 0.85.
5. **ANIMATION**: choose *Idle, Walk, Run, Attack, Hurt, Death* or *Custom…*, the *Direction* (Front, Back, Left, Right or all four) and the number of *Frames* (e.g. Idle 4, Walk 6, Attack 6).
6. **POSE**: every frame gets a pose from the animation template. *Pose Editor* shows the OpenPose skeleton exactly as it is sent to the AI: drag head, neck, shoulders, elbows, hands, hips, knees and feet. *Preset Pose* applies Idle, Walk 1–4, Run 1–4, Attack 1–3, Hurt, Death (or your own saved presets) to the selected frame. *Mirror*, *Reset to Template* and *Save as Preset…* are below the editor. Use *Underlay* to see the generated frame or the reference behind the skeleton.
7. **GENERATE → Generate Animation** generates the frames one after the other (one image at a time – fits 8 GB GPUs). All frames use the same seed so details stay stable.
8. **FRAMES**: click a frame to inspect it and its pose. **Regenerate Selected Frame** redoes only that frame with the same identity, reference, pose, animation, direction, prompt and strengths – only the seed changes. Every frame's seed, prompts, strengths, model, workflow, resolution, palette, direction, animation and index are stored (*History / Metadata*).
9. **EXPORT**: *Export PNG* (single frames), *Export Sprite Sheet* (`knight_walk.png` + `.json`, one row per direction) or *Export to Godot*.

Files: `characters/knight_001/reference/`, `identity/`, `animations/<animation>/<direction>/{poses.json,pose,raw,final}/`, `generated/` (every AI output, never overwritten), `export/`.

Tips: if the character drifts away from the reference, raise *Reference Strength* (0.9–1.1) or simplify the description; if the pose is ignored, raise *Pose Strength*; if the background is not removed cleanly, use a reference with a plain background.

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

## Using another drive (e.g. when C: is full)

Only three places grow large, and all of them can live on another drive:

| What | Size | How to move it |
|---|---|---|
| ComfyUI + AI models | 15–25 GB | Extract **ComfyUI Portable** to e.g. `E:\AI\ComfyUI_windows_portable` and select that folder in *Settings → ComfyUI* (models go into its `ComfyUI\models\...`). Or keep ComfyUI elsewhere and put models on E: via ComfyUI's `extra_model_paths.yaml`, then set *Settings → ComfyUI models folder (override)*. |
| Your projects (generated assets) | a few GB | *Settings → Default projects folder* → e.g. `E:\PixelRPG\Projects`. Then create a new project on the *Projects* page. Existing projects can simply be moved with Explorer and reopened via *Open Folder…*. |
| The app itself | ~150 MB | Put `PixelRPGAssetStudio.exe` anywhere, e.g. `E:\PixelRPG\`. |

Settings and logs stay in `%APPDATA%\PixelRPGAssetStudio` (a few MB). To move those too, set the environment variable `PIXEL_RPG_STUDIO_HOME` to a folder on E:.
