# ComfyUI workflows

Workflows live in `workflows/` as pairs:

```
character_concept.json            ComfyUI graph in API format
character_concept.manifest.json   which inputs are parameters / model roles / outputs
```

| Workflow | Kind | Used by |
|---|---|---|
| `character_concept` | text → image | Character Studio |
| `character_concept_reference` | reference image → image (img2img) | Character Studio with a reference |
| `object_concept`, `object_concept_reference` | text/reference → image | weapons, items, props, environment |
| `building_concept` | text → image (wide) | Buildings |
| `background_generation` | text → wide image | Backgrounds |
| `tileset_generation` | text → texture | Tiles / Tilesets |
| `pixel_cleanup` | low-denoise img2img | optional style unification pass |
| `image_to_3d_hunyuan` | image → GLB mesh | 3D generation (native ComfyUI Hunyuan3D v2 nodes) |

All image workflows use **core ComfyUI nodes only** (no custom node packs needed).

## Manifest format

```json
{
  "name": "character_concept",
  "title": "Character concept (SDXL + pixel-art LoRA)",
  "kind": "image",
  "description": "...",
  "requirements": ["human readable requirement", "..."],
  "parameters": {
    "prompt":        {"node": "6",  "input": "text", "type": "string"},
    "seed":          {"node": "3",  "input": "seed", "type": "int", "min": 0, "max": 4294967295},
    "width":         {"node": "5",  "input": "width", "type": "int", "default": 1024, "min": 256, "max": 2048},
    "lora_strength": {"node": "10", "input": ["strength_model", "strength_clip"], "type": "float", "default": 0.9},
    "init_image":    {"node": "11", "input": "image", "type": "image"}
  },
  "models":   {"image.checkpoint": {"node": "4", "input": "ckpt_name"}},
  "outputs":  {"images": {"node": "9"}},
  "profiles": {"low": {"width": 768, "height": 768, "steps": 22}}
}
```

- **Parameter types**: `string`, `int`, `float`, `bool`, `image` (an uploaded file name), `any`. `min`/`max` clamp values.
- **Model roles** are resolved from *Settings / AI Models*. When a role is unset, the value stored in the workflow file is used.
- **Profiles** are applied for the matching VRAM profile (`low`, `medium`, `high`) before explicit parameters.
- Pipelines use these standard parameter names: `prompt`, `negative_prompt`, `seed`, `width`, `height`, `batch_size`, `init_image` (reference workflows), and `image` (image-to-3D).

## Replacing or adding a workflow

1. Build the graph in ComfyUI and test it there.
2. Use **Workflow → Export (API)** and save it as `<name>.json`. The UI-format export (with `nodes` and `links`) is rejected with a clear message.
3. Write `<name>.manifest.json`, mapping your node IDs to the standard parameter names.
4. Put both files in either location:
   - `<project>/workflows/` – applies to that project only
   - `%APPDATA%\PixelRPGAssetStudio\workflows\` – applies to all projects
5. Run **Diagnostics**. It validates the manifest against the graph, and the graph against your ComfyUI (missing nodes and models).

If you use the same name as a bundled workflow, yours replaces it.
