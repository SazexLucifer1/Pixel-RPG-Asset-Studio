# Design decisions

Short records of the choices made where requirements left room, so later maintainers (human or Claude Code) know *why*.

### D1 – Single process, no internal web service
FastAPI was suggested "if useful". The UI and pipelines live in one process and talk through a job queue, so an internal HTTP layer would add a port, startup ordering and failure modes with no benefit. The only network communication is with ComfyUI's local API.

### D2 – One sequential job worker
Every heavy stage runs on one worker thread. On a 12 GB GPU, running image and 3D models concurrently would cause out-of-memory errors. Sequential stages plus `/free` between them keep VRAM predictable. Cancellation interrupts ComfyUI or kills Blender.

### D3 – Workflows as API-format JSON + manifest
Node IDs appear only in `workflows/`. Manifests name parameters and model roles, so the UI is independent of any particular graph, and users can drop in their own graphs without code changes.

### D4 – Core ComfyUI nodes, except the character identity (superseded for characters by D17)
Object, tile and background workflows use core nodes only (reference guidance = img2img). Characters need real image conditioning, which core ComfyUI does not offer for SDXL, so the character workflow requires one custom node pack (ComfyUI_IPAdapter_plus, see D17). Diagnostics names the pack and how to install it.

### D5 – Hunyuan3D via ComfyUI's *native* nodes
The native Hunyuan3D v2 nodes need no custom node pack. The mini shape model fits an RTX 3060. Texture generation was skipped on purpose (heavy, slow). Colours are projected from the concept image in Blender, which is good enough at pixel resolution. The licence's territory exclusion is surfaced in the UI and docs, and model import is always available.

### D6 – Toon shading via emission + dot(normal, light) instead of lamps
The shader computes lighting bands from a fixed light vector, with no light objects. It is identical in Cycles and EEVEE, deterministic, works headless without a GPU (Cycles CPU), and keeps light fixed relative to the camera when the model rotates for directions. Cycles with a few samples is the default because EEVEE needs an OpenGL context, which some headless setups lack.

### D7 – Rotate the model for directions, never the camera (3D objects)
One camera and one framing for every direction, so views line up pixel-perfectly. The framing (ortho scale and target) is computed once over all directions, stored in `asset.json`, and reused.

### D8 – (removed) Procedural humanoid rig and animations
The 3D character path (concept → Hunyuan3D → colour projection → auto rig → procedural animation → render) was removed in favour of D17: front-only colour projection smeared side/back views, the generic palette lost the reference colours, AI meshes rigged poorly, and the result never looked like the reference.

### D9 – Deterministic pixel pipeline as recorded steps
Processing is a list of `{op, params}` steps that is stored in metadata and replayable. "Mode" downscaling (most frequent colour per block) avoids the blended colours that resampling produces. Animation frames are downscaled as whole frames (no re-centering) to stay aligned.

### D10 – Palette locking per character
A character's palette is extracted from its reference image when the identity is created (`identity/identity.json`) and every frame is quantised to it: colours match the reference and never flicker between frames or animations. (3D objects lock a palette from their first render when the project palette is not forced.)

### D11 – Procedural VFX
Local diffusion models do not produce temporally coherent effect frames. Seeded particle simulation rendered at pixel resolution is reproducible and looks coherent. Frames can still be imported.

### D12 – Tilesets as 16-tile "match corners" sets
Godot 4's *Match Corners* terrain mode needs only 16 tiles, and the tiles can be generated deterministically from two seamless textures with periodic noise. That guarantees edges connect. Variants are added as extra tiles with the same terrain bits, and Godot picks among them randomly.

### D13 – Minimal stdlib WebSocket client
ComfyUI progress uses WebSocket. A roughly 100-line receive-only client avoids another dependency in the bundle. Polling is the fallback, so a failure costs only the progress display.

### D14 – Settings outside the repository
`%APPDATA%/PixelRPGAssetStudio/settings.json`, overridable with `PIXEL_RPG_STUDIO_HOME` (tests, portable setups). Unknown keys are preserved, wrong types are ignored, and a corrupt file is backed up and replaced by defaults, so the app always starts.

### D15 – PyInstaller onefile by default
The requirement is "one .exe". Onefile starts a few seconds slower because it unpacks to a temp folder, so a onedir build is available (`-Mode onedir`) for users who prefer a faster start.

### D16 – Mock providers are explicit
Mock and demo mode is labelled in the sidebar and recorded as `mock_*` providers in metadata. It exists for UI testing and CI, never as a silent fallback for real generation.

### D17 – Characters: 2D generation from a reference image (PixelLab-like)
Exactly one character workflow: reference image → identity → OpenPose pose → AI frame → pixel processing. Choices:
- **Identity = reference image as real image conditioning**: IP-Adapter Plus SDXL (h94, Apache-2.0) with the CLIP-ViT-H encoder, applied with `IPAdapterAdvanced` from ComfyUI_IPAdapter_plus (GPL-3, the de-facto standard; ComfyUI core has no SDXL IP-Adapter). Reference Strength = IP-Adapter weight. Text only supports the image. An optional equipment image goes through a second IP-Adapter pass with its own weight (v1 of separate weapon/shield control).
- **Pose = OpenPose ControlNet** (xinsir/controlnet-openpose-sdxl-1.0, Apache-2.0 – chosen over thibaud's model because of its license and quality) via core `ControlNetApplyAdvanced`. Pose Strength = ControlNet strength.
- **Own skeleton instead of a pose detector**: poses come from a small FK skeleton (`poses/`) projected per direction, so the same pose works for front/back/left/right (diagonals ready), every frame has the same scale and ground line, and users edit joints directly. No extra model or custom node is needed for poses.
- **One frame per request, same seed for all frames of an animation**: fits 8 GB (RTX 3070) with a 768 px canvas on the *low* profile; the shared seed plus shared reference keeps details stable. Regeneration repeats the recorded prompt/strengths with a new seed only.
- **Fixed canvas, no per-frame cropping**: frames are downscaled as whole canvases so they stay aligned; background removal + binary alpha + palette quantisation + outline make real pixel art.
- **Next step (not yet built)**: a per-character LoRA trained on accepted frames for even tighter identity.
