"""Shared GUI helpers."""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from emt.core import Interface, list_interfaces


def wrap_all_tooltips(root: QWidget) -> None:
    """Qt only word-wraps *rich-text* tooltips; plain ones are drawn as one long line.
    Convert every plain tooltip under `root` (call once after building the UI)."""
    for w in [root, *root.findChildren(QWidget)]:
        text = w.toolTip()
        if text and not text.lstrip().startswith("<"):  # already rich text
            w.setToolTip(wrap_tooltip(text))


def wrap_tooltip(text: str) -> str:
    """Plain text -> word-wrapping rich-text tooltip. Use for tooltips set after startup."""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


class InterfaceCombo(QComboBox):
    """Drop-down of local interfaces; item data is an `Interface` or None (= OS default)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.reload()

    def reload(self) -> None:
        current = self.selected()
        self.clear()
        self.addItem("Auto (OS default route)", None)
        for itf in list_interfaces():
            self.addItem(itf.label, itf)
        if current is not None:
            for i in range(1, self.count()):
                if self.itemData(i).name == current.name:
                    self.setCurrentIndex(i)
                    break

    def selected(self) -> Interface | None:
        return self.currentData()

    def short_label(self) -> str:
        itf = self.selected()
        return "auto" if itf is None else itf.nice_name


def make_table(columns: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def set_cell(table: QTableWidget, row: int, col: int, text: str) -> None:
    item = table.item(row, col)
    if item is None:
        table.setItem(row, col, QTableWidgetItem(text))
    elif item.text() != text:
        item.setText(text)


def selected_rows(table: QTableWidget) -> list[int]:
    """Rows selected in the table; if nothing is selected and there is only one row, that row."""
    rows = sorted({idx.row() for idx in table.selectionModel().selectedRows()})
    if not rows and table.rowCount() == 1:
        rows = [0]
    return rows


def append_row(table: QTableWidget, select: bool = True) -> int:
    """Add an empty row and (by default) select it, so the action buttons apply to it straight away."""
    row = table.rowCount()
    table.insertRow(row)
    if select:
        table.selectRow(row)
    return row


def install_context_menu(table: QTableWidget, build: Callable[[QMenu, list[int]], None]) -> None:
    """Right-click menu acting on the selected rows. Right-clicking a row that isn't
    selected selects just that row first (the usual desktop convention)."""
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(pos: QPoint) -> None:
        idx = table.indexAt(pos)
        if not idx.isValid():
            return
        selected = {i.row() for i in table.selectionModel().selectedRows()}
        if idx.row() not in selected:
            table.selectRow(idx.row())
        rows = sorted({i.row() for i in table.selectionModel().selectedRows()})
        menu = QMenu(table)
        build(menu, rows)
        menu.exec(table.viewport().mapToGlobal(pos))

    table.customContextMenuRequested.connect(show)


def swatch_item(color: QColor) -> QTableWidgetItem:
    """Coloured, checkable cell: the stream's graph colour, ticked = shown on the graph."""
    item = QTableWidgetItem()
    item.setBackground(color)
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setCheckState(Qt.CheckState.Checked)
    item.setToolTip(wrap_tooltip("Graph colour - untick to hide this stream on the graph"))
    return item


def fmt_int(n: int) -> str:
    return f"{n:,}"
