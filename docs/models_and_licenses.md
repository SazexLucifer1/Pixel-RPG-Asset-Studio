# Models, tools and licenses

The application **does not contain and does not download** any AI model. You download each model yourself from its official page and accept its license. The catalog the app uses is in [`config/models_catalog.json`](../config/models_catalog.json). The *AI Models* page shows the same information.

> License terms change. Always read the current license on the official page. The summaries below are not legal advice.

## AI models (default workflows)

| Role | Model | Folder | License (summary) | Official page |
|---|---|---|---|---|
| `image.checkpoint` | Stable Diffusion XL base 1.0 | `models/checkpoints` | CreativeML Open RAIL++-M: use restrictions in the license; commercial use of outputs permitted under its terms | <https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0> |
| `image.pixel_lora` | Pixel Art XL (LoRA) | `models/loras` | See model card (CreativeML Open RAIL family) | <https://huggingface.co/nerijs/pixel-art-xl> |
| `image.ipadapter` | IP-Adapter Plus SDXL ViT-H (`sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors`) – character identity from the reference image | `models/ipadapter` | Apache-2.0 | <https://huggingface.co/h94/IP-Adapter> |
| `image.clip_vision` | CLIP ViT-H image encoder (`models/image_encoder/model.safetensors`, save as `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors`) | `models/clip_vision` | Apache-2.0 (h94 repo); original LAION weights MIT | <https://huggingface.co/h94/IP-Adapter> |
| `image.controlnet_openpose` | ControlNet OpenPose SDXL by xinsir (`diffusion_pytorch_model.safetensors`, save as `xinsir-openpose-sdxl-1.0.safetensors`) – character poses | `models/controlnet` | Apache-2.0 | <https://huggingface.co/xinsir/controlnet-openpose-sdxl-1.0> |
| `threed.shape_model` | Hunyuan3D-2mini (shape) | `models/checkpoints` (save as `hunyuan3d-dit-v2-mini.safetensors`) | **Tencent Hunyuan 3D 2.0 Community License**: does **not** apply in the **EU, UK and South Korea**; additional conditions (e.g. for large services) | <https://huggingface.co/tencent/Hunyuan3D-2mini> |

VRAM: SDXL + IP-Adapter + CLIP Vision + ControlNet fit an 8 GB GPU (e.g. RTX 3070) with the *low* profile: 768 px canvas, one frame per request, models unloaded between jobs; ComfyUI offloads what does not fit.

All roles are configurable. Any SDXL checkpoint or pixel-art LoRA can be used instead (*AI Models → Choose Installed File…*). The 3D role can be replaced with another ComfyUI workflow, or bypassed entirely by importing 3D models.

### If Hunyuan3D's license does not fit you

- **Import 3D models** (GLB/FBX/OBJ) that you made or are licensed to use. Simple blockout meshes from Blender work very well at pixel-art resolutions.
- Use a different image-to-3D model with a suitable license through ComfyUI: add a workflow plus manifest (see [workflows.md](workflows.md)) whose output node saves a mesh, and point the `threed.shape_model` role at its model file.
- For prototyping, the *Placeholder blockout mesh* provider (*Settings → 3D generation*) runs the full pipeline with a procedural mesh and no AI.

## Tools

| Tool | License | Notes |
|---|---|---|
| ComfyUI | GPL-3.0 | Runs as a separate program; the app talks to it over its local HTTP API. Not bundled. |
| ComfyUI_IPAdapter_plus (custom nodes, cubiq) | GPL-3.0 | Required for characters (`IPAdapterModelLoader`, `IPAdapterAdvanced`). Install in ComfyUI via Manager. Not bundled. <https://github.com/cubiq/ComfyUI_IPAdapter_plus> |
| Blender | GPL-2.0-or-later | Runs as a separate program (headless). Not bundled. |
| Godot | MIT | Export target only. |

## Libraries bundled into the exe

| Library | License |
|---|---|
| PySide6 / Qt 6 | LGPL-3.0 (dynamically linked) |
| Pillow | MIT-CMU (HPND) |
| NumPy | BSD-3-Clause |
| requests, urllib3 | Apache-2.0 / MIT |
| psutil | BSD-3-Clause |
| PyInstaller bootloader | GPL-2.0 with bootloader exception (allows distributing the built exe) |

## Your generated assets

The licenses of the models you use can restrict how their outputs may be used. Pixel RPG Asset Studio records the model and workflow of every generation in each asset's `asset.json`, so you can always trace which model produced an asset.
