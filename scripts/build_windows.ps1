<#
.SYNOPSIS
  Reproducible Windows build of PixelRPGAssetStudio.exe.

.DESCRIPTION
  1. creates an isolated virtual environment (build\_work\venv)
  2. installs pinned-range dependencies from requirements-dev.txt
  3. runs the automated tests (skip with -SkipTests)
  4. builds the exe with PyInstaller (build\pixel_rpg_asset_studio.spec)
  5. smoke-tests the built exe (--version and --self-test)
  6. writes dist\PixelRPGAssetStudio-<version>-windows.zip

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Mode onedir -SkipTests
#>
param(
    [ValidateSet("onefile", "onedir")] [string] $Mode = "onefile",
    [switch] $SkipTests,
    [switch] $Console,
    [string] $Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$Work = Join-Path $Root "build\_work"
$Venv = Join-Path $Work "venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

Step "Creating build environment in $Venv"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
if (-not (Test-Path $Py)) {
    $parts = @($Python.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries))
    $rest = @()
    if ($parts.Length -gt 1) { $rest = $parts[1..($parts.Length - 1)] }
    & $parts[0] @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create a virtual environment with '$Python'. Install Python 3.10-3.12 from python.org." }
}
& $Py -m pip install --upgrade pip | Out-Null
& $Py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }

if (-not $SkipTests) {
    Step "Running tests"
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed - not building." }
    Remove-Item Env:\QT_QPA_PLATFORM
}

Step "Building ($Mode)"
$env:STUDIO_BUILD_MODE = $Mode
$env:STUDIO_CONSOLE = $(if ($Console) { "1" } else { "0" })
& $Py -m PyInstaller build\pixel_rpg_asset_studio.spec --noconfirm --clean --distpath dist --workpath (Join-Path $Work "pyinstaller")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = if ($Mode -eq "onedir") { "dist\PixelRPGAssetStudio\PixelRPGAssetStudio.exe" } else { "dist\PixelRPGAssetStudio.exe" }
if (-not (Test-Path $Exe)) { throw "Build output $Exe not found" }

Step "Smoke-testing $Exe"
$env:PIXEL_RPG_STUDIO_HOME = Join-Path $Work "smoke_home"
$p = Start-Process -FilePath $Exe -ArgumentList "--version" -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) { throw "--version failed with exit code $($p.ExitCode)" }
$p = Start-Process -FilePath $Exe -ArgumentList "--self-test" -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) { throw "--self-test failed with exit code $($p.ExitCode). Run with -Console to see output." }
Remove-Item Env:\PIXEL_RPG_STUDIO_HOME

$Version = (& $Py -c "import sys; sys.path.insert(0, 'src'); import pixel_rpg_studio as m; print(m.__version__)").Trim()
$Zip = "dist\PixelRPGAssetStudio-$Version-windows-$Mode.zip"
Step "Packaging $Zip"
if (Test-Path $Zip) { Remove-Item $Zip }
$items = @($(if ($Mode -eq "onedir") { "dist\PixelRPGAssetStudio" } else { $Exe }), "README.md", "LICENSE", "docs\user_guide.md", "docs\troubleshooting.md", "docs\models_and_licenses.md")
Compress-Archive -Path $items -DestinationPath $Zip
Write-Host "`nDone: $Exe" -ForegroundColor Green
Write-Host "Package: $Zip" -ForegroundColor Green
