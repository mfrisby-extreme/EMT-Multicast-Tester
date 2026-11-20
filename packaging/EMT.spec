# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the EMT GUI. Run via:  python scripts/build.py
#
# A plain PySide6 + pyqtgraph build is ~125 MB, mostly Qt pieces this app
# never uses. PyInstaller's Qt hooks pull them in through *plugins* (e.g. the
# PDF image-format plugin drags in QtPdf; the virtual-keyboard input plugin
# drags in QtQuick/QtQml), which --exclude-module can't stop, so the collected
# files are filtered here instead.
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is provided by PyInstaller)
PKG = ROOT / "packaging"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/emt/__init__.py").read_text()).group(1)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# Python modules the app doesn't use (pyqtgraph imports most of these Qt ones optionally).
EXCLUDE_MODULES = [
    "tkinter",
    "PySide6.QtNetwork", "PySide6.QtSvg",
    "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.Qt3DCore",
    "pyqtgraph.opengl", "pyqtgraph.examples", "pyqtgraph.jupyter",
]

# Collected files to drop, matched against their path inside the bundle.
# (QtOpenGL/QtOpenGLWidgets stay: pyqtgraph imports them unconditionally, though nothing uses OpenGL.)
_libs = ["Pdf", "Quick", "Qml", "VirtualKeyboard", "Network", "Svg", "Test"]
DROP = re.compile(
    r"Qt6?(" + "|".join(_libs) + r")"
    r"|[/\\]translations[/\\]"  # English-only app
    r"|[/\\]plugins[/\\](imageformats|platforminputcontexts|tls|networkinformation|iconengines|generic)[/\\]"
    r"|libqminimal",
    re.IGNORECASE,
)


def _keep(toc):
    return [entry for entry in toc if not DROP.search(entry[0])]


a = Analysis(  # noqa: F821
    [str(PKG / "emt_gui.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / "src/emt/gui/resources"), "emt/gui/resources")],
    excludes=EXCLUDE_MODULES,
    # pyqtgraph loads this via importlib, which PyInstaller can't see.
    hiddenimports=["PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"],
    noarchive=False,
)
a.binaries = _keep(a.binaries)
a.datas = _keep(a.datas)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EMT",
    console=False,
    upx=False,  # UPX-packed executables are a common antivirus false-positive trigger
    icon=str(PKG / "emt.ico") if IS_WIN else None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="EMT", upx=False)  # noqa: F821

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name="EMT.app",
        icon=str(PKG / "emt.icns"),
        bundle_identifier="com.example.emt",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "EMT Multicast Tester",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
        },
    )
