# Run the automated test suite on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$env:QT_QPA_PLATFORM = "offscreen"
Set-Location $Root
& $Py -m pytest -q @args
exit $LASTEXITCODE
