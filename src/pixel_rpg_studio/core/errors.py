"""User-facing error model.

Every error shown in the UI should be a :class:`StudioError` with:

* ``message`` - what went wrong, in plain language
* ``hint``    - what the user can do about it
* ``details`` - the technical detail (never hidden; shown behind "Details")

:func:`translate_exception` converts arbitrary exceptions into StudioErrors so
the UI never shows a bare stack trace or "HTTP 500".
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field


class StudioError(Exception):
    """An error with a human-readable explanation and a suggested fix."""

    def __init__(
        self,
        message: str,
        hint: str = "",
        details: str = "",
        code: str = "error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details
        self.code = code

    def full_text(self) -> str:
        parts = [self.message]
        if self.hint:
            parts.append(f"What to do: {self.hint}")
        if self.details:
            parts.append(f"Technical details:\n{self.details}")
        return "\n\n".join(parts)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(StudioError):
    pass


class ProjectError(StudioError):
    pass


class BackendUnavailableError(StudioError):
    pass


class WorkflowError(StudioError):
    pass


class GenerationError(StudioError):
    pass


class MissingModelError(GenerationError):
    pass


class MissingCustomNodeError(GenerationError):
    pass


class BlenderError(StudioError):
    pass


class ExportConflictError(StudioError):
    def __init__(self, conflicts: list[str]) -> None:
        listing = "\n".join(conflicts[:50])
        more = f"\n... and {len(conflicts) - 50} more" if len(conflicts) > 50 else ""
        super().__init__(
            f"{len(conflicts)} file(s) already exist in the export destination.",
            hint="Confirm overwriting, or choose a different export folder.",
            details=listing + more,
            code="export_conflict",
        )
        self.conflicts = conflicts


class JobCancelledError(StudioError):
    def __init__(self) -> None:
        super().__init__("The job was cancelled.", code="cancelled")


@dataclass
class ErrorReport:
    """Serializable error description passed from worker threads to the UI."""

    message: str
    hint: str = ""
    details: str = ""
    code: str = "error"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ErrorReport":
        err = translate_exception(exc)
        return cls(err.message, err.hint, err.details, err.code)

    def full_text(self) -> str:
        return StudioError(self.message, self.hint, self.details, self.code).full_text()


def translate_exception(exc: BaseException) -> StudioError:
    """Convert any exception into a StudioError with a useful message."""
    if isinstance(exc, StudioError):
        if not exc.details:
            exc.details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        return exc

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()

    try:
        import requests

        if isinstance(exc, requests.ConnectionError):
            return BackendUnavailableError(
                "Could not connect to the local AI backend (ComfyUI).",
                hint="Start ComfyUI from the status bar or the Diagnostics page, "
                "and check the ComfyUI address in Settings.",
                details=tb,
                code="backend_unavailable",
            )
        if isinstance(exc, requests.Timeout):
            return BackendUnavailableError(
                "The local AI backend (ComfyUI) did not respond in time.",
                hint="ComfyUI may be busy loading a model. Wait a moment and try again, "
                "or check the ComfyUI log.",
                details=tb,
                code="backend_timeout",
            )
    except ImportError:  # pragma: no cover
        pass

    if isinstance(exc, FileNotFoundError):
        return StudioError(
            f"A required file was not found: {exc.filename or exc}",
            hint="Check that the file exists and that the configured paths in Settings are correct.",
            details=tb,
            code="file_not_found",
        )
    if isinstance(exc, PermissionError):
        return StudioError(
            f"Permission denied: {exc.filename or exc}",
            hint="Choose a folder you can write to, or close programs that may be locking the file.",
            details=tb,
            code="permission_denied",
        )
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return StudioError(
            "The disk is full.",
            hint="Free some disk space and try again.",
            details=tb,
            code="disk_full",
        )
    if isinstance(exc, MemoryError):
        return StudioError(
            "The application ran out of memory.",
            hint="Close other programs, enable Low VRAM mode in Settings, or reduce the output resolution.",
            details=tb,
            code="out_of_memory",
        )
    return StudioError(
        f"Unexpected error: {type(exc).__name__}: {exc}",
        hint="Use 'Copy Error' and 'View Log' to inspect the problem. Running the diagnostic may help.",
        details=tb,
        code="unexpected",
    )
