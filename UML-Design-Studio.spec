# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller packaging recipe.

It works outside Windows too: the version resource and the .ico icon are
Windows-only features, so they are skipped silently when the file is
missing or the platform is not Windows (otherwise PyInstaller stops with
"version resource not found").

    .venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm UML-Design-Studio.spec

Output: dist/UML-Design-Studio.exe  (one file, no console, GPL text embedded)
"""

import os
import sys

_IS_WIN = sys.platform.startswith("win")
# The version resource and the .ico are meaningful ONLY on Windows; both optional.
_version_file = "version_info.txt" if (_IS_WIN and os.path.exists("version_info.txt")) else None
_icon_file = "docs/app.ico" if (_IS_WIN and os.path.exists("docs/app.ico")) else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("LICENSE", "."),                 # the Help > License dialog reads this file
        # Clause -> page index: the specification page number shown next to a
        # validation finding COMES FROM THIS. PyInstaller does not pick up a data
        # file sitting NEXT TO a .py by itself; left off the list,
        # `uml_spec._index()` silently returned an empty dict and every finding in
        # the DELIVERED PRODUCT lost its page number.
        ("app/core/uml_spec_index.json", "app/core"),
        ("examples/blinky.usm", "examples"),
        ("examples/blinky_ctx.h", "examples"),
        ("examples/blinky_demo.c", "examples"),
        ("examples/blinky_demo.cpp", "examples"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep the large unused Qt modules out of the package
        # CAREFUL: QtPdf / QtPdfWidgets ARE NOT EXCLUDED -- the "UML 2.5.1
        # Specification (PDF)" window under the References menu uses them.
        # Removed from the list, that window fails to open in the EXE with
        # "No module named PyQt6.QtPdf".
        "PyQt6.QtNetwork", "PyQt6.QtQml", "PyQt6.QtQuick",
        "PyQt6.QtMultimedia", "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets",
        "PyQt6.QtSql", "PyQt6.QtTest", "PyQt6.QtXml", "PyQt6.QtDBus",
        "PyQt6.QtDesigner", "PyQt6.QtHelp", "PyQt6.QtPrintSupport",
        "PyQt6.QtSvg", "PyQt6.QtSvgWidgets", "PyQt6.QtWebChannel",
        "PyQt6.QtWebSockets", "PyQt6.QtPositioning", "PyQt6.QtSerialPort",
        "PyQt6.QtBluetooth", "PyQt6.QtNfc", "PyQt6.QtRemoteObjects",
        "PyQt6.QtSensors", "PyQt6.QtSpatialAudio", "PyQt6.QtTextToSpeech",
        "tkinter", "unittest", "pydoc_data",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="UML-Design-Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_file,
    version=_version_file,
)
