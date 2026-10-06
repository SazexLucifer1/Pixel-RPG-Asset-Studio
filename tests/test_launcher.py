import json
import sys

from pixel_rpg_studio.comfyui.launcher import (
    DESKTOP_DEFAULT_PORT,
    ComfyUIProcess,
    apply_install_to_settings,
    build_launch_command,
    detect_install,
)
from pixel_rpg_studio.core.config import AppSettings


def make_portable(root):
    (root / "python_embeded").mkdir(parents=True)
    (root / "python_embeded" / "python.exe").write_text("")
    (root / "ComfyUI").mkdir()
    (root / "ComfyUI" / "main.py").write_text("")
    (root / "run_nvidia_gpu.bat").write_text("")
    return root


def test_detect_portable_from_root_or_subfolder(tmp_path):
    root = make_portable(tmp_path / "ComfyUI_windows_portable")
    for chosen in (root, root / "ComfyUI"):
        inst = detect_install(chosen)
        assert inst.kind == "portable" and inst.root == root
        assert inst.models_dir == root / "ComfyUI" / "models"
        assert inst.can_autostart


def test_portable_launch_command(tmp_path):
    inst = detect_install(make_portable(tmp_path / "p"))
    s = AppSettings()
    s.comfyui.extra_launch_args = "--preview-method auto"
    cmd, cwd = build_launch_command(inst, s, low_vram=True)
    assert cmd[0].endswith("python.exe") and "--windows-standalone-build" in cmd
    assert "--disable-auto-launch" in cmd and "--lowvram" in cmd
    assert cmd[cmd.index("--port") + 1] == "8188"
    assert cmd[-2:] == ["--preview-method", "auto"]
    assert cwd == inst.root


def test_detect_manual(tmp_path):
    root = tmp_path / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("")
    inst = detect_install(root)
    assert inst.kind == "manual" and not inst.can_autostart and inst.notes
    venv = root / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    venv.mkdir(parents=True)
    (venv / ("python.exe" if sys.platform == "win32" else "python")).write_text("")
    assert detect_install(root).can_autostart


def test_detect_desktop(tmp_path, monkeypatch):
    exe_dir = tmp_path / "Programs" / "@comfyorgcomfyui-electron"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "ComfyUI.exe"
    exe.write_text("")
    base = tmp_path / "Documents" / "ComfyUI"
    (base / "models").mkdir(parents=True)
    import pixel_rpg_studio.comfyui.launcher as L

    monkeypatch.setattr(L, "desktop_config_base_path", lambda: base)
    inst = detect_install(exe)
    assert inst.kind == "desktop" and inst.data_dir == base and inst.default_port == DESKTOP_DEFAULT_PORT
    s = AppSettings()
    apply_install_to_settings(inst, s)
    assert s.comfyui.port == 8000 and s.comfyui.install_type == "desktop"
    cmd, _ = build_launch_command(inst, s, low_vram=False)
    assert cmd == [str(exe)]


def test_nothing_detected(tmp_path):
    assert detect_install(tmp_path) is None
    assert detect_install(tmp_path / "missing") is None
    assert detect_install("") is None


def test_process_start_wait_and_crash(tmp_path):
    # A "ComfyUI" that exits immediately must produce a clear error, not hang.
    from pathlib import Path

    from pixel_rpg_studio.comfyui.launcher import ComfyInstall

    root = tmp_path / "manual"
    root.mkdir()
    (root / "main.py").write_text("import sys; print('broken custom node'); sys.exit(3)")
    # Use the running interpreter directly (symlinks need extra privileges on Windows).
    inst = ComfyInstall("manual", root, python=Path(sys.executable), main_py=root / "main.py", data_dir=root)
    proc = ComfyUIProcess()
    proc.start(inst, AppSettings(), low_vram=False)
    from pixel_rpg_studio.core.errors import BackendUnavailableError

    try:
        proc.wait_until_ready(lambda: False, timeout=20)
        raise AssertionError("should have failed")
    except BackendUnavailableError as exc:
        assert "exited" in exc.message
        assert "broken custom node" in exc.details
    finally:
        proc.stop()


def make_comfy_desktop_env(local, name="PixelRPG"):
    """Layout of the new Comfy Desktop (seen in a real user's error message)."""
    env = local / "Comfy-Desktop" / "ComfyUI-Installs" / name
    comfy = env / "ComfyUI"
    (comfy / "comfy").mkdir(parents=True)
    (comfy / "main.py").write_text("")
    scripts = comfy / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    (scripts / ("python.exe" if sys.platform == "win32" else "python")).write_text("")
    (env / "standalone-env").mkdir()
    return env


def test_new_comfy_desktop_environment(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    env = make_comfy_desktop_env(local)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    # any of these selections must work
    for chosen in (env, env / "ComfyUI", local / "Comfy-Desktop" / "ComfyUI-Installs", local / "Comfy-Desktop"):
        inst = detect_install(chosen)
        assert inst is not None, chosen
        assert inst.kind == "manual" and inst.root == env / "ComfyUI"
        assert inst.can_autostart and inst.models_dir == env / "ComfyUI" / "models"
    from pixel_rpg_studio.comfyui.launcher import find_desktop_environments, find_installations

    assert [i.root for i in find_desktop_environments()] == [env / "ComfyUI"]
    assert find_installations()[0].root == env / "ComfyUI"
    cmd, cwd = build_launch_command(detect_install(env), AppSettings(), low_vram=True)
    assert "--lowvram" in cmd and "--disable-auto-launch" in cmd and cwd == env / "ComfyUI"


def test_interrupted_desktop_install_is_explained(tmp_path):
    env = tmp_path / "ComfyUI-Installs" / "Broken"
    (env / "ComfyUI").mkdir(parents=True)
    (env / "ComfyUI" / "main.py").write_text("")
    inst = detect_install(env)
    assert inst is not None and not inst.can_autostart
    assert any("interrupted" in n for n in inst.notes)


def test_new_desktop_exe_names(tmp_path):
    folder = tmp_path / "Comfy Desktop"
    folder.mkdir()
    exe = folder / "Comfy Desktop.exe"
    exe.write_text("")
    (folder / "Uninstall Comfy Desktop.exe").write_text("")
    assert detect_install(exe).kind == "desktop"
    inst = detect_install(folder)
    assert inst.kind == "desktop" and inst.executable == exe
