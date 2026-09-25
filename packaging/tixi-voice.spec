# PyInstaller specification for Tixi Voice.
#
# Design notes (they exist to keep the installer under 110 MB):
#   * only the core runtime is bundled — PySide6-Essentials, numpy, sounddevice,
#     soundfile, requests, lameenc. No torch, no onnxruntime, no ctranslate2;
#   * Qt is trimmed to the modules the UI actually imports (Widgets, Gui, Core,
#     Network, Svg) and unneeded Qt plugins/translations are excluded;
#   * engine packs and models are downloaded after installation into
#     %LOCALAPPDATA%\TixiVoice — never into the installer;
#   * src/tixi/assets/ is bundled when it exists (icons and fonts are drawn in
#     code by ui/theme, so a checkout without the folder still builds).
#
# Build:  pyinstaller packaging/tixi-voice.spec --noconfirm --clean
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 - provided by PyInstaller
SRC = ROOT / "src"
ASSETS = SRC / "tixi" / "assets"

block_cipher = None

datas: list[tuple[str, str]] = []
if ASSETS.exists():
    datas.append((str(ASSETS), "tixi/assets"))

hiddenimports = [
    "tixi.app.application",
    "tixi.ui.theme.styles",
    "tixi.ui.theme.icons",
]
hiddenimports += collect_submodules("tixi.services")
hiddenimports += [
    # Imported lazily so the application starts without them; listing them keeps
    # the modules available for the engine packs' sys.path entries.
    "sqlite3",
    "ctypes",
]

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test", "tests",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PIL", "matplotlib", "scipy", "pandas", "IPython",
    "torch", "torchaudio", "transformers", "onnxruntime", "ctranslate2", "av",
    "setuptools", "pip", "pkg_resources",
]

a = Analysis(  # noqa: F821 - PyInstaller globals
    [str(SRC / "tixi" / "app" / "application.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TixiVoice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # UPX breaks Qt DLLs and trips antivirus heuristics
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "tixi-voice.ico") if (ROOT / "packaging" / "tixi-voice.ico").exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TixiVoice",
)
