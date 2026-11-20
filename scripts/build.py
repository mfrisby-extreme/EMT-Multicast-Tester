"""Build distributable binaries for the current OS with PyInstaller.

    python scripts/build.py          # GUI + CLI
    python scripts/build.py --cli    # CLI only (fast, no Qt)

Outputs in dist/:
    EMT/ (Windows, Linux) or EMT.app (macOS)  - GUI, one-folder build
    emt-cli/emt-cli.exe (Windows)             - CLI, one-folder build
    emt-cli (macOS, Linux)                    - CLI, single file

One-folder builds are used where antivirus matters most: a one-file build
unpacks itself to a temp folder and runs from there, which is what malware
droppers do, so Windows antivirus flags it far more often. One-folder also
starts instantly and keeps the Qt libraries as separate, replaceable files
(the simplest way to meet PySide6's LGPL terms). UPX compression is always
off: UPX-packed executables are another strong false-positive trigger.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
COMMON = ["--noconfirm", "--clean", "--noupx", "--paths", str(ROOT / "src"),
          "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
          "--specpath", str(ROOT / "build")]



def _fix_qt_paths_with_commas() -> None:
    """Work around Qt truncating its install paths at a comma.

    PySide6 hands Qt its install prefix through an INI-style config in which a
    comma is a list separator, so in a folder like ".../OneDrive-Company,Inc/..."
    Qt reports e.g. ".../OneDrive-Company/plugins" and PyInstaller's Qt hook fails
    with "Qt plugin directory ... does not exist". After PyInstaller reads the
    paths from Qt, put the part after the comma back.
    """
    try:
        import PySide6
    except ImportError:  # CLI-only build environment without Qt
        return
    from PyInstaller import compat
    from PyInstaller.utils.hooks import qt as pyi_qt

    real = str(Path(PySide6.__file__).resolve().parent / "Qt")
    if "," not in real:
        return
    cut = real.split(",", 1)[0]
    original = pyi_qt.QtLibraryInfo._load_qt_info

    def patched(self) -> None:
        original(self)
        loc = self.__dict__.get("location")
        if self.namespace != "PySide6" or not loc:
            return
        for key, value in loc.items():
            if value and value.startswith(cut) and not value.startswith(real):
                loc[key] = real + value[len(cut):]
        # Re-derive the fields PyInstaller computed from the truncated paths.
        prefix = Path(loc["PrefixPath"]).resolve()
        self.qt_inside_package = self.package_location == prefix or self.package_location in prefix.parents
        if compat.is_win:
            lib = loc["PrefixPath"] if self.qt_inside_package and not self.is_pyqt else loc["BinariesPath"]
        else:
            lib = loc["LibrariesPath"]
        self.qt_lib_dir = Path(lib).resolve()

    pyi_qt.QtLibraryInfo._load_qt_info = patched


def build_cli() -> None:
    layout = "--onedir" if sys.platform.startswith("win") else "--onefile"
    PyInstaller.__main__.run(COMMON + [
        layout, "--console", "--name", "emt-cli",
        "--exclude-module", "PySide6", "--exclude-module", "tkinter",
        "--exclude-module", "numpy", "--exclude-module", "pyqtgraph",
        str(PKG / "emt_cli.py"),
    ])


def build_gui() -> None:
    # Options and the list of unused Qt pieces to strip live in packaging/EMT.spec.
    # (UPX is disabled inside the spec; PyInstaller rejects --noupx alongside a spec file.)
    PyInstaller.__main__.run(["--noconfirm", "--clean", "--distpath", str(ROOT / "dist"),
                              "--workpath", str(ROOT / "build"), str(PKG / "EMT.spec")])
    if sys.platform == "darwin" and (ROOT / "dist/EMT.app").is_dir():
        # On macOS dist/EMT/ is only PyInstaller's staging copy of what went into
        # EMT.app - a 90 MB duplicate. (On Windows/Linux dist/EMT/ *is* the app.)
        shutil.rmtree(ROOT / "dist/EMT", ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", action="store_true", help="build only the CLI")
    ap.add_argument("--gui", action="store_true", help="build only the GUI")
    a = ap.parse_args()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    _fix_qt_paths_with_commas()
    if not a.gui:
        build_cli()
    if not a.cli:
        build_gui()


if __name__ == "__main__":
    main()
