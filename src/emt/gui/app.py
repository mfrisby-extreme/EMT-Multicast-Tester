"""GUI entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PySide6
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from emt import APP_NAME, __version__
from emt.gui.main_window import MainWindow


def _fix_qt_plugin_path() -> None:
    """Point Qt at PySide6's bundled plugins explicitly.

    PySide6 passes its install prefix to Qt through an INI-style config, and
    Qt treats a comma there as a list separator. A path such as
    ".../OneDrive-Company,Inc/..." is cut short and Qt then cannot find its
    platform plugin ("Could not find the Qt platform plugin"). Must run
    before QApplication is created.
    """
    plugins = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    if plugins.is_dir():
        QCoreApplication.addLibraryPath(str(plugins))
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(plugins / "platforms"))


RESOURCES = Path(__file__).resolve().parent / "resources"


def app_icon() -> QIcon:
    """Window / taskbar / Dock icon, from the PNGs made by scripts/make_icons.py."""
    icon = QIcon()
    for png in sorted((RESOURCES / "icons").glob("emt_*.png")):
        icon.addFile(str(png))
    return icon


def _set_windows_app_id() -> None:
    """Without an explicit AppUserModelID, Windows groups the window under python.exe
    and shows Python's icon in the taskbar when running from source."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EMT.MulticastTester")
        except Exception:
            pass


def run() -> int:
    _fix_qt_plugin_path()
    _set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("EMT")
    win = MainWindow()
    win.show()
    if os.environ.get("EMT_SMOKE_TEST"):
        # Used by CI to check a built app starts: open the window, then quit cleanly.
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2000, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
