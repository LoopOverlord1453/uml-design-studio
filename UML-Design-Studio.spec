# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller packaging recipe -- one spec, three platforms.

Run it through the build script rather than by hand:

    build.bat        (Windows)
    ./build.sh       (Linux / macOS)

PyInstaller is a CROSS-PLATFORM tool but NOT a cross-COMPILER: it bundles the
interpreter and the Qt libraries of the machine it runs on. A Windows .exe has
to be built on Windows, a Linux binary on Linux, a macOS app on macOS. There is
no flag that changes this.

What each platform produces:

    Windows   dist/UML-Design-Studio.exe        one file, no console window,
                                                .ico icon + version resource
    Linux     dist/UML-Design-Studio            one ELF file, already executable
    macOS     dist/UML Design Studio.app        a real app bundle, so a
                                                double-click opens no Terminal
              dist/UML-Design-Studio            the plain binary, for a terminal

`console=False` is what keeps the black window away on Windows; on macOS the
same job is done by the .app bundle, which is why BUNDLE is added below.

The version resource and the .ico are Windows-only ideas, so they are skipped
elsewhere -- otherwise PyInstaller stops with "version resource not found".
"""

import os
import sys

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"

# The version resource and the .ico are meaningful ONLY on Windows; both optional.
_version_file = "version_info.txt" if (_IS_WIN and os.path.exists("version_info.txt")) else None

# Every platform wants its own icon format: .ico on Windows, .icns on macOS.
# A missing icon is not an error -- the app simply gets the default one.
if _IS_WIN and os.path.exists("docs/app.ico"):
    _icon_file = "docs/app.ico"
elif _IS_MAC and os.path.exists("docs/app.icns"):
    _icon_file = "docs/app.icns"
else:
    _icon_file = None

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

# On macOS a bare Unix binary opens a Terminal window when double-clicked;
# only a .app bundle launches as a normal application. This is the macOS
# counterpart of `console=False` on Windows.
if _IS_MAC:
    app = BUNDLE(                                          # noqa: F821 - PyInstaller
        exe,
        name="UML Design Studio.app",
        icon=_icon_file,
        bundle_identifier="io.github.loopoverlord1453.umldesignstudio",
        info_plist={
            "CFBundleDisplayName": "UML Design Studio",
            "CFBundleShortVersionString": "2.0.0",
            "CFBundleVersion": "2.0.0",
            "NSHumanReadableCopyright":
                "(c) 2026 the UML Design Studio contributors"
                " - GNU GPL v3",
            # Retina: without this the whole interface is drawn at half
            # resolution and every label looks blurred.
            "NSHighResolutionCapable": True,
        },
    )
