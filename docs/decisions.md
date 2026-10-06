# Design decisions

Short records of the choices made where requirements left room, so later maintainers (human or Claude Code) know *why*.

### D1 – Single process, no internal web service
FastAPI was suggested "if useful". The UI and pipelines live in one process and talk through a job queue, so an internal HTTP layer would add a port, startup ordering and failure modes with no benefit. The only network communication is with ComfyUI's local API.

### D2 – One sequential job worker
Every heavy stage runs on one worker thread. On a 12 GB GPU, running image and 3D models concurrently would cause out-of-memory errors. Sequential stages plus `/free` between them keep VRAM predictable. Cancellation interrupts ComfyUI or kills Blender.

### D3 – Workflows as API-format JSON + manifest
Node IDs appear only in `workflows/`. Manifests name parameters and model roles, so the UI is independent of any particular graph, and users can drop in their own graphs without code changes.

### D4 – Core ComfyUI nodes only for image workflows
IP-Adapter or ControlNet custom nodes would improve reference fidelity, but they break with ComfyUI updates and complicate setup. Reference guidance uses img2img (core nodes). Custom-node workflows can be added later as optional workflows.

### D5 – Hunyuan3D via ComfyUI's *native* nodes
The native Hunyuan3D v2 nodes need no custom node pack. The mini shape model fits an RTX 3060. Texture generation was skipped on purpose (heavy, slow). Colours are projected from the concept image in Blender, which is good enough at pixel resolution. The licence's territory exclusion is surfaced in the UI and docs, and model import is always available.

### D6 – Toon shading via emission + dot(normal, light) instead of lamps
The shader computes lighting bands from a fixed light vector, with no light objects. It is identical in Cycles and EEVEE, deterministic, works headless without a GPU (Cycles CPU), and keeps light fixed relative to the camera when the model rotates for directions. Cycles with a few samples is the default because EEVEE needs an OpenGL context, which some headless setups lack.

### D7 – Rotate the model for directions, never the camera
One camera and one framing for every direction and animation, so sprites line up pixel-perfectly. The framing (ortho scale and target) is computed once over all animations and directions, stored in `asset.json`, and reused for later renders and single-frame regeneration.

### D8 – Procedural humanoid rig and animations
Fully automatic rigging is unreliable. The heuristic rig plus heat weights (falling back to envelope weights) is honest about its quality: a rig report with unweighted vertex count and warnings. Every prepared scene is saved as `.blend`, so the user can correct it in Blender, and later renders use the corrected file. Imported rigs with named actions take priority.

### D9 – Deterministic pixel pipeline as recorded steps
Processing is a list of `{op, params}` steps that is stored in metadata and replayable. "Mode" downscaling (most frequent colour per block) avoids the blended colours that resampling produces. Animation frames are downscaled as whole frames (no re-centering) to stay aligned.

### D10 – Palette locking per character
When the project palette is not forced, a palette is extracted from the first render and stored in the asset. All later frames use it, which prevents colour flicker between animations.

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
