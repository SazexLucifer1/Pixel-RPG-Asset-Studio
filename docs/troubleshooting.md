# Troubleshooting

Start with **Diagnostics → Run Full Diagnostic**. Every problem it finds comes with an explanation and a fix button. Use *Export Report…* to share the result.

Logs live in `%APPDATA%\PixelRPGAssetStudio\logs\`:

| File | Content |
|---|---|
| `studio.log` | the application (*View Log* in the status bar) |
| `comfyui.log` | ComfyUI output (only when the app started ComfyUI) |
| `blender.log` | every Blender run |

## Starting the app

| Symptom | Cause / fix |
|---|---|
| "Failed to extract …: decompression resulted in return code -1" when starting `PixelRPGAssetStudio.exe` | The single-file exe unpacks itself into the Windows temp folder on C: first; **C: is full**. Use the **folder version** (`PixelRPGAssetStudio-…-onedir.zip`, extract e.g. to `E:\PixelRPG` and start `PixelRPGAssetStudio\PixelRPGAssetStudio.exe`); it unpacks nothing. Or free space on C:. |

## ComfyUI

| Symptom | Cause / fix |
|---|---|
| "ComfyUI not running" | Press *ComfyUI ▾ → Start ComfyUI* in the status bar. Check *Settings → ComfyUI*: install folder, host and port. **ComfyUI Desktop uses port 8000**; Portable uses 8188. |
| "ComfyUI installation not found" | *Settings → Choose Folder…*. For Portable, pick the folder that contains `run_nvidia_gpu.bat` and `python_embeded`. For the **new Comfy Desktop**, pick `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\<installation name>` (paste the path into the folder dialog's address bar); the studio then starts that ComfyUI directly. Older Desktop versions: *Choose .exe…*. |
| "installation was interrupted" / ENOSPC during Comfy Desktop setup | The disk ran full. Delete the half-installed folder under `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\`, free ≥ 30 GB (or use ComfyUI Portable on another drive) and install again. |
| "ComfyUI exited during startup" | Open the ComfyUI log. The most common cause is a broken custom node or a missing dependency after a ComfyUI update. Start ComfyUI manually once to see the error. |
| "did not become ready within … s" | The first start can take minutes. Raise *Settings → Start timeout*. |
| "Required model missing: …" | The workflow needs a model file that ComfyUI does not have. Use *AI Models* for download links, or *Choose Installed File…* to pick an alternative. |
| "workflow uses a node type that is not installed" | Your ComfyUI is too old for the workflow (e.g. the Hunyuan3D nodes need ComfyUI ≥ 0.3.27), or a custom node pack is missing. Update ComfyUI. Diagnostics lists the missing node types. |
| "The GPU ran out of memory" | Set *Settings → VRAM profile → low*, keep *Unload AI models between stages* on, and close other GPU programs (browsers with hardware acceleration, games). |
| "ComfyUI reported an internal error (HTTP 500)" | Usually a model/node problem inside ComfyUI. Check the ComfyUI log. |
| Generation is very slow | Check that ComfyUI uses the GPU (Diagnostics shows the CUDA device). A first run also loads the model from disk. |

## Blender

| Symptom | Cause / fix |
|---|---|
| "Blender was not found" | Install Blender 3.6+ or select `blender.exe` in *Settings → Blender*. |
| "Blender exited without producing a result" | See `blender.log`. Update the GPU driver. Make sure the model file is not corrupt. |
| Object faces away | *Model facing → Rotate 180°*, then *Prepare Model* again. |

## Characters

| Symptom | Cause / fix |
|---|---|
| "missing node types: IPAdapterAdvanced, IPAdapterModelLoader" | The custom node pack **ComfyUI_IPAdapter_plus** is not installed. ComfyUI → Manager → Custom Nodes Manager → search *IPAdapter plus* (cubiq) → Install → restart ComfyUI. Without Manager: `git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus` into `ComfyUI/custom_nodes`. |
| "Required model missing: ip-adapter-plus_sdxl_vit-h.safetensors" / CLIP-ViT-H / xinsir-openpose | Download from the *AI Models* page and put the file into `models/ipadapter`, `models/clip_vision` or `models/controlnet` with exactly the listed file name (or choose your file with *Choose Installed File…*). Restart ComfyUI after creating the `ipadapter` folder. |
| "This character has no identity yet" | Choose a *Reference Image* and press *Create Character* first. |
| The character does not look like the reference | Raise *Reference Strength* (0.9–1.1), keep the description short and matching the image, use a reference with a plain background and the full body visible. |
| The pose is ignored / limbs in wrong places | Raise *Pose Strength* (1.0–1.2) or lower *Reference Strength* a bit. Check the frame in the *Pose Editor*. |
| One frame is bad | Select it under FRAMES → *Regenerate Selected Frame* (new seed, everything else identical), or fix its pose first. |
| Background pixels remain around the sprite | The AI drew a background or a floor shadow. Regenerate the frame; references with a plain background help. |
| Out of memory on 8 GB | VRAM profile *low* (768 px canvas, fewer steps), keep *Unload AI models between stages* on, close browsers/games. Frames are always generated one at a time. |

## Pixel output

| Symptom | Fix |
|---|---|
| Colours look wrong / too few | *Style*: turn *Force project palette* off (an automatic per-asset palette is then locked on the first render), or edit the palette. |
| Blurry edges / semi-transparent pixels | They never come out of the pipeline. If you see them in Godot, set the texture filter to **Nearest**. |
| Outline too heavy | *Style → Outline*: `selective` (darker shade of each neighbour colour) or `none`. |
| Background not removed | Concepts should have a plain, light background (the default prompts ask for it). Import your own images with transparency to skip removal. |

## Godot

- **"files already exist"**: the exporter never overwrites silently. Confirm, or pick another sub-folder under *Projects → Godot project*.
- **Textures look blurry**: set *Project Settings → Rendering → Textures → Canvas Textures → Default Texture Filter* to **Nearest**.
- **Tileset terrain painting**: open the `TileSet` `.tres` and select the TileMap → *Terrains* tab → terrain set 0.

## Settings are broken

Delete or rename `%APPDATA%\PixelRPGAssetStudio\settings.json`, and the app starts with defaults. A damaged settings file is backed up automatically as `settings.json.corrupt`.

## Still stuck?

Run `PixelRPGAssetStudio.exe --diagnose --out report.md` and attach `report.md` plus the logs to a GitHub issue.
