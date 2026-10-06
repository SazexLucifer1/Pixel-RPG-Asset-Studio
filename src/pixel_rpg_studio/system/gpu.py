"""GPU / VRAM detection and hardware-aware generation profiles.

Primary source: ``nvidia-smi`` (installed with every NVIDIA driver). Fallback:
ComfyUI's ``/system_stats`` (the server knows its CUDA device). The app never
assumes a high-end GPU; unknown hardware is treated as the "low" profile.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class GPUInfo:
    name: str
    vram_total_mb: int
    vram_used_mb: int = 0
    driver_version: str = ""
    utilization_pct: int | None = None
    temperature_c: int | None = None
    source: str = "nvidia-smi"

    @property
    def vram_total_gb(self) -> float:
        return round(self.vram_total_mb / 1024, 1)

    @property
    def vram_free_mb(self) -> int:
        return max(0, self.vram_total_mb - self.vram_used_mb)


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in (
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ):
            if candidate.exists():
                return str(candidate)
    return None


def parse_nvidia_smi_csv(text: str) -> list[GPUInfo]:
    """Parse ``--query-gpu=name,memory.total,memory.used,driver_version,utilization.gpu,temperature.gpu``."""
    gpus = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue

        def as_int(v: str) -> int | None:
            try:
                return int(float(v))
            except ValueError:
                return None

        gpus.append(
            GPUInfo(
                name=parts[0],
                vram_total_mb=as_int(parts[1]) or 0,
                vram_used_mb=as_int(parts[2]) or 0,
                driver_version=parts[3],
                utilization_pct=as_int(parts[4]) if len(parts) > 4 else None,
                temperature_c=as_int(parts[5]) if len(parts) > 5 else None,
            )
        )
    return gpus


def query_gpus(timeout: float = 5.0) -> list[GPUInfo]:
    exe = find_nvidia_smi()
    if not exe:
        return []
    cmd = [exe, "--query-gpu=name,memory.total,memory.used,driver_version,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"]
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        result = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("nvidia-smi failed: %s", exc)
        return []
    if result.returncode != 0:
        log.warning("nvidia-smi returned %s: %s", result.returncode, result.stderr.strip())
        return []
    return parse_nvidia_smi_csv(result.stdout)


def gpus_from_comfy_devices(devices: list[dict]) -> list[GPUInfo]:
    out = []
    for d in devices:
        if d.get("type") == "cuda" or "cuda" in str(d.get("name", "")).lower():
            total = int(d.get("vram_total", 0) / 1024**2)
            free = int(d.get("vram_free", 0) / 1024**2)
            out.append(GPUInfo(name=str(d.get("name", "CUDA device")), vram_total_mb=total, vram_used_mb=max(0, total - free), source="ComfyUI"))
    return out


# --------------------------------------------------------------- profiles
@dataclass(frozen=True)
class HardwareProfile:
    key: str
    label: str
    comfy_low_vram_flag: bool
    max_image_side: int
    unload_between_stages: bool
    description: str


PROFILES = {
    "low": HardwareProfile("low", "Low VRAM (≤ 8 GB)", True, 768, True,
                           "Smaller generation resolution, ComfyUI --lowvram (CPU offloading), models unloaded between stages."),
    "medium": HardwareProfile("medium", "Medium VRAM (≈ 12 GB, e.g. RTX 3060)", False, 1024, True,
                              "Full SDXL resolution; models unloaded between stages so image and 3D models never share VRAM."),
    "high": HardwareProfile("high", "High VRAM (≥ 16 GB)", False, 1024, False,
                            "Full resolution; models may stay loaded between stages."),
}


def profile_for_vram(vram_total_mb: int | None) -> HardwareProfile:
    if not vram_total_mb or vram_total_mb < 10 * 1024:
        return PROFILES["low"]
    if vram_total_mb < 15 * 1024:
        return PROFILES["medium"]
    return PROFILES["high"]


def resolve_profile(setting: str, gpus: list[GPUInfo] | None = None) -> HardwareProfile:
    """Settings value ``auto`` picks a profile from the detected VRAM."""
    if setting in PROFILES:
        return PROFILES[setting]
    gpus = gpus if gpus is not None else query_gpus()
    vram = max((g.vram_total_mb for g in gpus), default=0)
    return profile_for_vram(vram)
