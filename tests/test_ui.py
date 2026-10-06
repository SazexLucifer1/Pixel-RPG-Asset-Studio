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


def test_character_studio_full_run(window, qtbot):
    page = window.pages["Characters"]
    window.services.project.create_asset("character", "Knight", "knight")
    page.refresh_list()
    page.asset_list.setCurrentRow(0)
    page.run_full()
    wait_jobs(qtbot, window)
    assert window.errors == []
    assert page.anim_combo.count() == 2 and len(page.preview.frames) > 0
    page.preview.step(1)
    page.regenerate_frame()
    wait_jobs(qtbot, window)
    assert window.errors == []


def test_error_path_shows_dialog(window, qtbot):
    page = window.pages["Characters"]
    window.services.project.create_asset("character", "Empty")
    page.refresh_list()
    page.asset_list.setCurrentRow(0)
    page.prepare_model()  # no model yet -> explained error, no crash
    wait_jobs(qtbot, window)
    assert len(window.errors) == 1
    assert "3D model" in window.errors[0].message


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
