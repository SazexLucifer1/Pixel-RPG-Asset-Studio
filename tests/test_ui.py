"""UI smoke tests (offscreen Qt) driving the real widgets with mock providers."""

import time

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.ui


@pytest.fixture
def window(qtbot, mock_settings, tmp_path):
    from pixel_rpg_studio.ui.main_window import MainWindow
    from pixel_rpg_studio.ui.services import Services

    services = Services(mock_settings, gpus=[])
    w = MainWindow(services)
    errors = []
    w.show_error = errors.append
    services.error_handler = w.show_error
    w.errors = errors
    qtbot.addWidget(w)
    services.create_project(tmp_path / "projects", "UI Test")
    yield w
    w.backend.shutdown()
    services.shutdown()


def wait_jobs(qtbot, w, timeout=120):
    end = time.time() + timeout
    qtbot.wait(50)
    while w.services.jobs.pending_count() and time.time() < end:
        qtbot.wait(50)
    qtbot.wait(100)


def test_all_pages_load(window):
    from pixel_rpg_studio.ui.main_window import NAV

    for name in NAV:
        window.go(name)
        assert not isinstance(window.pages[name], type(None))
    assert window.pages["Dashboard"].project_label.text().startswith("<b>UI Test")


def test_character_studio_full_run(window, qtbot, tmp_path):
    from pixel_rpg_studio.providers.mock import mock_reference

    page = window.pages["Characters"]
    ref = tmp_path / "knight.png"
    mock_reference(256).save(ref)
    asset = window.services.project.create_asset("character", "Knight")
    page.refresh_list()
    page.asset_list.setCurrentRow(0)
    from pixel_rpg_studio.pipeline import character as ch

    ch.set_reference(asset, ref)
    page.reload_asset()
    page.description.setPlainText("knight with sword and shield")
    page.create_character()
    wait_jobs(qtbot, window)
    assert window.errors == []
    assert page._identity() is not None
    page.anim_combo.setCurrentIndex(page.anim_combo.findData("walk"))
    page.dir_combo.setCurrentIndex(page.dir_combo.findData("e"))
    page.frames_spin.setValue(4)
    page.ref_strength.setValue(0.9)
    page.generate_animation()
    wait_jobs(qtbot, window)
    assert window.errors == []
    assert page.frame_list.count() == 4 and len(page.preview.frames) == 4
    assert ch.load_identity(page.asset)["reference_strength"] == 0.9
    # pose editing + preset + regenerate only that frame
    page.frame_list.setCurrentRow(2)
    assert page.pose_editor.pose is not None
    page.pose_editor.move_joint("r_wrist", 0.8, 0.3)
    assert ch.get_pose(page.asset, "walk", "e", 2).joints["r_wrist"] == [0.8, 0.3]
    page.preset_combo.setCurrentText("Attack 2")
    page.apply_preset()
    assert ch.get_pose(page.asset, "walk", "e", 2).source == "preset:Attack 2"
    page.frame_list.setCurrentRow(2)
    page.regenerate_frame()
    wait_jobs(qtbot, window)
    assert window.errors == []


def test_error_path_shows_dialog(window, qtbot):
    page = window.pages["Characters"]
    window.services.project.create_asset("character", "Empty")
    page.refresh_list()
    page.asset_list.setCurrentRow(0)
    page.generate_animation()  # no identity yet -> explained error, no crash
    assert len(window.errors) == 1
    assert "identity" in window.errors[0].message


def test_style_page_saves(window):
    page = window.pages["Style"]
    page.sw.setValue(32)
    page.palette_text.setPlainText("#000000, #ffffff, #ff0000")
    page.save()
    assert window.services.project.style.sprite_width == 32
    assert window.services.project.palette.colors == ["#000000", "#ffffff", "#ff0000"]


def test_settings_page_saves(window, isolated_home):
    page = window.pages["Settings"]
    page.port.setValue(8000)
    page.save()
    from pixel_rpg_studio.core.config import load_settings

    assert load_settings().comfyui.port == 8000


def test_error_dialog_builds(qtbot):
    from pixel_rpg_studio.core.errors import ErrorReport
    from pixel_rpg_studio.ui.widgets.dialogs import ErrorDialog

    dlg = ErrorDialog(ErrorReport("Boom", "Do this", "trace"), on_diagnostic=lambda: None)
    qtbot.addWidget(dlg)
    assert dlg.details.toPlainText() == "trace"


def test_setup_wizard_builds(qtbot, mock_settings):
    from pixel_rpg_studio.ui.services import Services
    from pixel_rpg_studio.ui.setup_wizard import SetupWizard

    wiz = SetupWizard(Services(mock_settings, gpus=[]))
    qtbot.addWidget(wiz)
    wiz._finish()
    assert wiz.services.settings.setup_completed


def test_export_all_accepted(window, qtbot, godot_project, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    page = window.pages["Weapons"]
    window.services.project.create_asset("weapon", "Axe", "axe")
    page.refresh_list()
    page.asset_list.setCurrentRow(0)
    page.use_3d.setChecked(False)
    page.run_full()
    wait_jobs(qtbot, window)
    page.accept_asset()
    projects = window.pages["Projects"]
    projects.godot_dir.setText(str(godot_project))
    projects.export_all()
    assert list((godot_project / "assets/generated/weapons").rglob("*.png"))
    assert window.errors == []


def test_setup_wizard_projects_folder_and_start_button(qtbot, mock_settings, tmp_path):
    from pixel_rpg_studio.ui.services import Services
    from pixel_rpg_studio.ui.setup_wizard import SetupWizard

    wiz = SetupWizard(Services(mock_settings, gpus=[]))
    qtbot.addWidget(wiz)
    wiz.projects.setText(str(tmp_path / "E_drive" / "Projects"))
    wiz.comfy.setText("")
    wiz._start_comfy()  # nothing selected -> explained, no crash
    assert "No ComfyUI installation" in wiz.check_status.text()
    wiz._finish()
    assert wiz.services.settings.paths.projects_dir.endswith("Projects")


def test_comfy_installed_but_not_running_is_warning(tmp_path):
    import sys

    from pixel_rpg_studio.comfyui.client import ComfyUIClient
    from pixel_rpg_studio.core.config import AppSettings
    from pixel_rpg_studio.system.diagnostics import check_comfyui

    root = tmp_path / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("")
    s = AppSettings()
    s.comfyui.install_dir = str(root)
    server = [r for r in check_comfyui(s, ComfyUIClient("http://127.0.0.1:9", 1)) if r.name == "Server"][0]
    assert server.status == "warning" and "Not running yet" in server.summary
    assert sys
