# Run the application from source on Windows (development).
#   powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1 [--mock]
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Venv = Join-Path $Root ".venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    py -3.12 -m venv $Venv
    & (Join-Path $Venv "Scripts\python.exe") -m pip install -r (Join-Path $Root "requirements-dev.txt")
}
$env:PYTHONPATH = Join-Path $Root "src"
& (Join-Path $Venv "Scripts\python.exe") -m pixel_rpg_studio @args
