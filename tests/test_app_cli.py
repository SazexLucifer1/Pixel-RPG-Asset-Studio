import json

from pixel_rpg_studio import __version__
from pixel_rpg_studio.app import main, parse_args, run_self_test


def test_version(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_parse_args_ignores_qt_args():
    args = parse_args(["--mock", "-platform", "offscreen"])
    assert args.mock


def test_self_test(tmp_path):
    assert run_self_test(tmp_path) == 0
    assert list((tmp_path / "godot_project").rglob("*.tres"))


def test_diagnose_writes_report(tmp_path):
    out = tmp_path / "diag.json"
    code = main(["--diagnose", "--out", str(out)])
    data = json.loads(out.read_text())
    assert data["results"]
    assert code in (0, 1)
