# Development setup

## Requirements

- Windows 10/11 64-bit (primary). Linux and macOS work for development.
- Python 3.10–3.12 (3.12 recommended; the CI also tests 3.11)
- Optional for real generation: ComfyUI, Blender 3.6+, an NVIDIA GPU

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"
.venv\Scripts\python -m pixel_rpg_studio --mock      # demo mode, no AI needed
.venv\Scripts\python -m pixel_rpg_studio             # real mode
```

`scripts\run_dev.ps1` (Windows) and `scripts/run_dev.sh` (Linux/macOS) do all of this for you.

To keep development settings separate from your real ones, set `PIXEL_RPG_STUDIO_HOME` to another folder.

## Tests

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python -m pytest -q
```

| Test file | Covers |
|---|---|
| `test_config.py`, `test_paths.py` | settings storage, corrupt files, validation, safe file names |
| `test_project.py` | project creation/opening, asset metadata, references, versioned outputs |
| `test_workflows.py` | bundled workflow validity, parameter binding, profiles, server validation |
| `test_comfyui_client.py`, `test_ws.py` | real HTTP communication with `tests/fake_comfyui.py`: generation, upload, missing models/nodes, execution errors, HTTP 500, cancel; WebSocket frames |
| `test_pixel.py`, `test_tiles_vfx.py` | deterministic pixel processing, seamless tiles, terrain sets, VFX |
| `test_spritesheet.py`, `test_godot_export.py` | sheet layout/metadata, `.tres` format, overwrite protection |
| `test_pipeline_e2e.py` | full pipelines with mock providers (character vertical slice, single-frame regeneration, objects, buildings, tiles, backgrounds, VFX) |
| `test_launcher.py`, `test_system.py` | ComfyUI install detection and launch commands, crash during start, GPU parsing, hardware profiles, model catalog, diagnostics |
| `test_blender_integration.py` | **real Blender**: rig, animation, framing stability, multi-view props, broken model. Skipped if Blender is not found; set `PIXEL_RPG_TEST_BLENDER` to choose an executable. |
| `test_ui.py` | offscreen UI: every page loads, the character studio runs a full pipeline, error dialogs, style/settings pages |
| `test_app_cli.py` | `--version`, `--self-test`, `--diagnose` |

The generated Godot resources were also checked by loading them in Godot 4.3 (`SpriteFrames` animations/fps/loop, `TileSet` terrain mode and peering bits). To repeat that check, export a character and a tileset into a Godot project, then `load()` the `.tres` files from a script.

## Building the Windows exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1            # single PixelRPGAssetStudio.exe
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Mode onedir # folder build (faster start)
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Console     # with console window, for debugging
```

The script creates an isolated venv, installs dependencies, runs the tests, builds with PyInstaller (`build/pixel_rpg_asset_studio.spec`), smoke-tests the result (`--version`, `--self-test`) and zips it into `dist/`.

GitHub Actions:

- `.github/workflows/tests.yml` runs the tests on Windows and Ubuntu (Python 3.11/3.12) for every push and PR.
- `.github/workflows/build-windows.yml` builds the exe on `main`, on tags `v*` (attached to the GitHub release) and on manual dispatch. Download it from the run's *Artifacts*.

## Code conventions

- No heavy work on the GUI thread. Use `Services.submit(title, func)`.
- Raise `StudioError` (or a subclass) with `message`, `hint` and `details` for anything the user can act on.
- Every generation or processing step records a `GenerationRecord`.
- Keep processing deterministic. Image operations live in `imaging/` as pure functions.
- Never hard-code model filenames or node IDs outside `workflows/` and `config/models_catalog.json`.
- Never commit model weights, generated assets, user projects or secrets (`.gitignore` covers the common cases).

## Releasing

1. Update `__version__` in `src/pixel_rpg_studio/__init__.py` and `CHANGELOG.md`.
2. Tag `vX.Y.Z` and push. The build workflow attaches the exe and zip to the release.
