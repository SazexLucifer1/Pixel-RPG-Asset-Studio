# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build definition for Pixel RPG Asset Studio.
#
#   pyinstaller build/pixel_rpg_asset_studio.spec --noconfirm
#
# Environment variables:
#   STUDIO_BUILD_MODE = onefile (default, single PixelRPGAssetStudio.exe)
#                       onedir  (folder build, starts faster)
#   STUDIO_CONSOLE    = 1 to build with a console window (debugging)
#
# No AI models, user projects or settings are bundled - only the application,
# its workflows, default configuration and Blender scripts.
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
MODE = os.environ.get("STUDIO_BUILD_MODE", "onefile").lower()
CONSOLE = os.environ.get("STUDIO_CONSOLE", "0") == "1"
NAME = "PixelRPGAssetStudio"

datas = [
    (str(ROOT / "workflows"), "workflows"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "src" / "pixel_rpg_studio" / "blender" / "scripts"), "pixel_rpg_studio/blender/scripts"),
    (str(ROOT / "LICENSE"), "."),
]

# Qt modules the app does not use (keeps the exe much smaller).
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest", "cryptography",  # cryptography: only optional for urllib3
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSvgWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
]

a = Analysis(
    [str(ROOT / "build" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = str(ROOT / "assets" / "icon.ico")

if MODE == "onedir":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=CONSOLE, icon=icon, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=CONSOLE, icon=icon, upx=False,
              runtime_tmpdir=None)
