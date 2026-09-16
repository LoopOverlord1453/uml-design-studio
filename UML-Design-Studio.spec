# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller paketleme tarifi.

    .venv\\Scripts\\python.exe -m PyInstaller --clean --noconfirm UML-Design-Studio.spec

Cikti: dist/UML-Design-Studio.exe  (tek dosya, konsolsuz, GPL metni gomulu)
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("LICENSE", "."),                 # Yardim > Lisans diyalogu bu dosyayi okur
        # Bolum -> sayfa dizini: dogrulama bulgularinin yanindaki
        # spesifikasyon sayfa numarasi BUNDAN gelir. PyInstaller bir .py
        # dosyasinin YANINDAKI veri dosyasini kendiliginden almaz; liste
        # disinda kalinca `uml_spec._index()` sessizce bos sozluk donuyor
        # ve TESLIM EDILEN uruende her bulgu sayfa numarasini kaybediyordu.
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
        # Kullanilmayan buyuk Qt modulleri paket disinda kalsin
        # DIKKAT: QtPdf / QtPdfWidgets HARIC TUTULMAZ -- References
        # menusundeki "UML 2.5.1 Specification (PDF)" penceresi
        # onlari kullanir. Listeden cikarilirsa EXE'de o pencere
        # "No module named PyQt6.QtPdf" ile acilmaz.
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
    icon="docs/app.ico",
    version="version_info.txt",
)
