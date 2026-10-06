import requests

from pixel_rpg_studio.core.errors import ErrorReport, ExportConflictError, StudioError, translate_exception


def test_connection_error_translated():
    err = translate_exception(requests.ConnectionError("refused"))
    assert "ComfyUI" in err.message
    assert err.hint
    assert "refused" in err.details


def test_file_errors():
    assert "not found" in translate_exception(FileNotFoundError(2, "x", "a.png")).message
    assert "Permission" in translate_exception(PermissionError(13, "x", "b.png")).message


def test_unknown_error_keeps_details():
    try:
        raise ValueError("weird")
    except ValueError as exc:
        err = translate_exception(exc)
    assert "ValueError" in err.message
    assert "Traceback" in err.details


def test_studio_error_full_text():
    e = StudioError("A", hint="B", details="C")
    assert e.full_text() == "A\n\nWhat to do: B\n\nTechnical details:\nC"
    r = ErrorReport.from_exception(e)
    assert r.message == "A" and r.hint == "B"


def test_conflict_error_lists_files():
    e = ExportConflictError([f"f{i}.png" for i in range(60)])
    assert "60" in e.message
    assert "and 10 more" in e.details
