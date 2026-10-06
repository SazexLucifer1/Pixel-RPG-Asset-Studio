from pixel_rpg_studio.blender.detect import parse_version
from pixel_rpg_studio.core.config import AppSettings
from pixel_rpg_studio.models.catalog import check_models, load_catalog, scan_folder
from pixel_rpg_studio.system.diagnostics import DiagnosticReport, check_gpu, run_full_diagnostic, summary_table
from pixel_rpg_studio.system.gpu import GPUInfo, gpus_from_comfy_devices, parse_nvidia_smi_csv, profile_for_vram, resolve_profile


def test_parse_nvidia_smi():
    gpus = parse_nvidia_smi_csv("NVIDIA GeForce RTX 3060, 12288, 1024, 552.22, 7, 45\n")
    g = gpus[0]
    assert g.name == "NVIDIA GeForce RTX 3060" and g.vram_total_mb == 12288 and g.driver_version == "552.22"
    assert g.utilization_pct == 7 and g.vram_total_gb == 12.0
    assert parse_nvidia_smi_csv("garbage") == []


def test_profiles():
    assert profile_for_vram(12288).key == "medium"  # RTX 3060
    assert profile_for_vram(8192).key == "low"
    assert profile_for_vram(24576).key == "high"
    assert profile_for_vram(None).key == "low"  # unknown hardware -> safe default
    assert resolve_profile("high", []).key == "high"
    assert resolve_profile("auto", [GPUInfo("x", 12288)]).key == "medium"


def test_comfy_devices():
    g = gpus_from_comfy_devices([{"name": "cuda:0 RTX", "type": "cuda", "vram_total": 12 * 1024**3, "vram_free": 8 * 1024**3}])
    assert g[0].vram_total_mb == 12288 and g[0].vram_used_mb == 4096


def test_blender_version_parse():
    assert parse_version("Blender 4.2.3 LTS\n\tbuild date") == (4, 2, 3)
    assert parse_version("Blender 3.6") == (3, 6, 0)
    assert parse_version("nope") is None


def test_gpu_check_messages():
    missing = check_gpu([])
    assert missing[0].status in ("missing", "warning")
    ok = check_gpu([GPUInfo("RTX 3060", 12288, 0, "552.22")])
    assert [r.status for r in ok] == ["ok", "ok", "ok"]


def test_model_catalog(tmp_path):
    entries, tools = load_catalog()
    roles = {e.role for e in entries}
    assert {"image.checkpoint", "image.pixel_lora", "threed.shape_model"} <= roles
    for e in entries:
        assert e.download_page.startswith("https://") and e.license
    hunyuan = next(e for e in entries if e.role == "threed.shape_model")
    assert "European Union" in hunyuan.license_warning
    models = tmp_path / "models"
    (models / "loras").mkdir(parents=True)
    (models / "loras" / "pixel-art-xl.safetensors").write_bytes(b"x")
    assert scan_folder(models, "loras") == ["pixel-art-xl.safetensors"]
    st = {s.entry.role: s for s in check_models(AppSettings(), entries, models)}
    assert st["image.pixel_lora"].installed is True
    assert st["image.checkpoint"].installed is False
    assert check_models(AppSettings(), entries, None)[0].installed is None


def test_models_from_server(fake_comfy):
    from pixel_rpg_studio.comfyui.client import ComfyUIClient

    entries, _ = load_catalog()
    st = check_models(AppSettings(), entries, None, ComfyUIClient(fake_comfy.url))
    assert all(s.installed for s in st)


def test_full_diagnostic_never_crashes(fake_comfy):
    from pixel_rpg_studio.comfyui.client import ComfyUIClient

    s = AppSettings()
    rep = run_full_diagnostic(s, ComfyUIClient(fake_comfy.url))
    cats = {r.category for r in rep.results}
    assert {"System", "Hardware", "Storage", "ComfyUI", "Workflows", "AI Models", "Blender", "Configuration"} <= cats
    server = next(r for r in rep.results if r.category == "ComfyUI" and r.name == "Server")
    assert server.status == "ok"
    assert all(r.status == "ok" for r in rep.by_category("Workflows"))
    md = rep.to_markdown()
    assert "| Check | Status | Result |" in md
    assert '"results"' in rep.to_json()
    labels = [row[0] for row in summary_table(rep)]
    assert labels[:2] == ["GPU", "VRAM"] or labels[0] == "GPU"
    assert "Pixel tools" in labels


def test_diagnostic_offline():
    from pixel_rpg_studio.comfyui.client import ComfyUIClient

    rep = run_full_diagnostic(AppSettings(), ComfyUIClient("http://127.0.0.1:9", 1))
    server = next(r for r in rep.results if r.name == "Server")
    assert server.status in ("error", "missing") and server.hint
    assert isinstance(rep, DiagnosticReport)


def test_disk_check_thresholds_per_folder(tmp_path, monkeypatch):
    import shutil as sh
    from collections import namedtuple

    from pixel_rpg_studio.system import diagnostics

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(diagnostics.shutil, "disk_usage", lambda p: Usage(100 * 1024**3, 0, int(9.6 * 1024**3)))
    s = AppSettings()
    s.paths.projects_dir = str(tmp_path / "projects")
    s.comfyui.install_dir = str(tmp_path / "comfy")
    res = {r.name: r for r in diagnostics.check_disk(s)}
    assert res["Disk space (Application data (settings, logs))"].status == "ok"  # tiny data: 9.6 GB is plenty
    assert res["Disk space (Projects folder)"].status == "warning"
    assert res["Disk space (ComfyUI folder (AI models))"].status == "warning"
    assert "Settings" in res["Disk space (Projects folder)"].hint
    assert sh  # keep import used
