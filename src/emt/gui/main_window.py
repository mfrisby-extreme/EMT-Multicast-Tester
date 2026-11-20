"""Main application window."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
)

from emt import APP_NAME, __version__
from emt.gui.common import wrap_all_tooltips
from emt.gui.receiver_tab import ReceiverTab
from emt.gui.sender_tab import SenderTab

REFRESH_MS = 500


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 720)

        self.sender_tab = SenderTab()
        self.receiver_tab = ReceiverTab()
        tabs = QTabWidget()
        tabs.addTab(self.sender_tab, "Send")
        tabs.addTab(self.receiver_tab, "Receive")
        self.setCentralWidget(tabs)

        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(5000)
        dock = QDockWidget("Log")
        dock.setObjectName("log")
        dock.setWidget(self.log_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

        self.receiver_tab.local_streams = lambda: [r.sender.cfg for r in self.sender_tab.rows if r.sender.running]
        self.sender_tab.log.connect(self.append_log)
        self.receiver_tab.log.connect(self.append_log)

        self._build_menu(dock)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(REFRESH_MS)

        wrap_all_tooltips(self)
        self.append_log(f"{APP_NAME} {__version__} started")

    def _build_menu(self, dock: QDockWidget) -> None:
        file_menu = self.menuBar().addMenu("&File")
        reload_act = QAction("Refresh interfaces", self, shortcut=QKeySequence.StandardKey.Refresh)
        reload_act.triggered.connect(self._reload_interfaces)
        file_menu.addAction(reload_act)
        file_menu.addSeparator()
        quit_act = QAction("Quit", self, shortcut=QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(dock.toggleViewAction())

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def append_log(self, msg: str) -> None:
        self.log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def _refresh(self) -> None:
        self.sender_tab.refresh()
        self.receiver_tab.refresh()

    def _reload_interfaces(self) -> None:
        self.sender_tab.iface.reload()
        self.receiver_tab.iface.reload()
        self.append_log("Interface list refreshed")

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Multicast send/receive test tool for validating IGMP/MLD, snooping and PIM.<br>"
            "Built with Qt for Python (PySide6, LGPLv3).",
        )

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.sender_tab.stop_all()
        self.receiver_tab.leave_all()
        super().closeEvent(event)
