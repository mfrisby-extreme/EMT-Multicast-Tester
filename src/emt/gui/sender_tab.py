"""Sender tab: configure and run one or more outgoing streams."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from emt.core import RateMeter, Sender, SenderConfig
from emt.core.packet import HEADER_SIZE, MAX_UDP_PAYLOAD_V4
from emt.core.sockets import family_for, group_range
from emt.gui.common import (
    InterfaceCombo,
    append_row,
    install_context_menu,
    fmt_int,
    make_table,
    selected_rows,
    set_cell,
    swatch_item,
    wrap_tooltip,
)
from emt.gui.edit_dialog import EditStreamsDialog
from emt.gui.graph import RateGraph, stream_color

COLUMNS = ["", "", "Group", "Port", "Interface", "Size", "Target", "Sent", "pps", "Mbps", "Errors", "Status"]
BUTTON_COL, SWATCH_COL, FIRST_DATA_COL = 0, 1, 2


@dataclass
class _Row:
    key: int
    sender: Sender
    iface_label: str
    color: QColor
    meter: RateMeter
    button: QPushButton | None = None


class SenderTab(QWidget):
    log = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[_Row] = []
        self._keys = itertools.count()

        self.iface = InterfaceCombo()
        self.group = QLineEdit("239.1.1.1")
        self.group_count = QSpinBox(minimum=1, maximum=1000, value=1)
        self.group_count.setToolTip("Create this many streams, one per consecutive group address")
        self.port = QSpinBox(minimum=1, maximum=65535, value=5000)
        self.size = QSpinBox(minimum=HEADER_SIZE, maximum=MAX_UDP_PAYLOAD_V4, value=1316, suffix=" bytes")
        self.rate = QDoubleSpinBox(minimum=0, maximum=10_000_000, value=100, decimals=2)
        self.rate.setToolTip("Per stream. 0 = send as fast as possible")
        self.rate_unit = QComboBox()
        self.rate_unit.addItems(["pps", "Mbps"])
        self.ttl = QSpinBox(minimum=1, maximum=255, value=16)
        self.dscp = QSpinBox(minimum=0, maximum=63, value=0)
        self.count = QSpinBox(minimum=0, maximum=2_000_000_000, value=0, specialValueText="unlimited")
        self.duration = QSpinBox(minimum=0, maximum=86_400 * 7, value=0, suffix=" s", specialValueText="unlimited")
        self.loop = QCheckBox("Loop back to this host")
        self.loop.setChecked(False)
        self.loop.setToolTip(
            "Also deliver this traffic to this computer. Leave off for network testing: otherwise a "
            "receiver on this machine sees the stream even if the network drops it. On Windows this "
            "does not affect local receivers - use the Receive tab's 'Ignore traffic from this host'.")

        rate_row = QHBoxLayout()
        rate_row.addWidget(self.rate, 1)
        rate_row.addWidget(self.rate_unit)

        form = QFormLayout()
        form.addRow("Interface", self.iface)
        form.addRow("First group", self.group)
        form.addRow("Number of groups", self.group_count)
        form.addRow("Port", self.port)
        form.addRow("Packet size", self.size)
        form.addRow("Rate per stream", rate_row)
        form.addRow("TTL", self.ttl)
        form.addRow("DSCP", self.dscp)
        form.addRow("Packet count", self.count)
        form.addRow("Duration", self.duration)
        form.addRow("", self.loop)

        start_btn = QPushButton("Start")
        start_btn.setDefault(True)
        start_btn.clicked.connect(self._start)
        form.addRow("", start_btn)

        box = QGroupBox("New streams")
        box.setLayout(form)

        self.table = make_table(COLUMNS)
        self.table.setColumnWidth(SWATCH_COL, 28)
        self.table.itemChanged.connect(self._item_changed)
        self.table.setToolTip("Right-click (or double-click) to edit, start or stop streams")
        install_context_menu(self.table, self._build_menu)
        self.table.cellDoubleClicked.connect(lambda r, c: c > SWATCH_COL and self._edit_rows([r]))

        btns = QHBoxLayout()
        for label, slot in (
            ("Edit…", self._edit_selected),
            ("Stop", self._stop_selected),
            ("Restart", self._restart_selected),
            ("Remove", self._remove_selected),
            ("Stop all", self._stop_all_clicked),
            ("Remove all", self._remove_all),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)

        table_panel = QWidget()
        tp = QVBoxLayout(table_panel)
        tp.setContentsMargins(0, 0, 0, 0)
        tp.addWidget(self.table, 1)
        tp.addLayout(btns)

        self.graph = RateGraph()

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(table_panel)
        split.addWidget(self.graph)
        split.setSizes([300, 300])

        layout = QHBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(split, 1)

    # ----- actions -----------------------------------------------------------------

    def _rows(self) -> list[int]:
        rows = selected_rows(self.table)
        if not rows and self.table.rowCount():
            self.log.emit("Select a stream in the table first")
        return rows

    def _configs(self) -> list[SenderConfig]:
        groups = group_range(self.group.text().strip(), self.group_count.value())  # raises ValueError
        family = family_for(groups[0])
        itf = self.iface.selected()
        ip, idx = itf.params_for(family) if itf else (None, 0)
        size = self.size.value()
        rate = self.rate.value()
        pps = SenderConfig.pps_from_mbps(rate, size) if self.rate_unit.currentText() == "Mbps" else rate
        return [
            SenderConfig(
                group=g, port=self.port.value(), iface_ip=ip, iface_index=idx, ttl=self.ttl.value(),
                loop=self.loop.isChecked(), dscp=self.dscp.value(), size=size, rate_pps=pps,
                count=self.count.value(), duration=float(self.duration.value()),
            )
            for g in groups
        ]

    def _find(self, cfg: SenderConfig) -> _Row | None:
        """Existing row for the same group + port + interface, if any."""
        key = (cfg.group, cfg.port, cfg.iface_ip, cfg.iface_index)
        for row in self.rows:
            c = row.sender.cfg
            if (c.group, c.port, c.iface_ip, c.iface_index) == key:
                return row
        return None

    def _start(self) -> None:
        try:
            configs = self._configs()
            for c in configs:  # validate every config before starting any
                c.validate()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot start streams", str(e))
            return
        new = restarted = updated = skipped = 0
        for cfg in configs:
            row = self._find(cfg)
            try:
                if row is None:
                    sender = Sender(cfg)
                    sender.start()
                    self._add_row(sender)
                    new += 1
                elif row.sender.running:
                    # Same stream already running: apply any changed settings live, don't duplicate.
                    if row.sender.cfg != cfg:
                        row.sender.update(cfg)
                        updated += 1
                    else:
                        skipped += 1
                else:
                    row.sender.cfg = cfg  # pick up any changed rate / size / TTL
                    row.sender.start()
                    row.meter.reset()
                    restarted += 1
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "Cannot start stream", f"{cfg.group}: {e}")
                break
        done = new + restarted + updated + skipped
        if done:
            first, last = configs[0], configs[done - 1]
            groups = first.group if done == 1 else f"{first.group} - {last.group} ({done} groups)"
            parts = [f"{n} {label}" for n, label in ((new, "new"), (restarted, "restarted"),
                                                      (updated, "updated"), (skipped, "already running")) if n]
            self.log.emit(f"Sending {groups} port {first.port} via {self.iface.short_label()} "
                          f"({first.rate_pps:,.0f} pps each, {first.size} B, TTL {first.ttl}) - {', '.join(parts)}")
        self.refresh(sample=False)

    def _add_row(self, sender: Sender) -> None:
        key = next(self._keys)
        row = _Row(key, sender, self.iface.short_label(), stream_color(key), RateMeter())
        self.rows.append(row)
        idx = append_row(self.table, select=False)
        self.table.blockSignals(True)
        self.table.setItem(idx, SWATCH_COL, swatch_item(row.color))
        self.table.blockSignals(False)
        row.button = QPushButton("Stop")
        row.button.setToolTip(wrap_tooltip("Stop / start this stream"))
        row.button.clicked.connect(lambda _=False, r=row: self._toggle(r))
        self.table.setCellWidget(idx, BUTTON_COL, row.button)
        self.graph.add(key, row.color)
        self.table.selectRow(idx)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == SWATCH_COL and item.row() < len(self.rows):
            self.graph.set_visible(self.rows[item.row()].key, item.checkState() == Qt.CheckState.Checked)

    def _stop_row(self, row: _Row) -> None:
        if row.sender.running:
            row.sender.stop()
            self.log.emit(f"Stopped {row.sender.cfg.group}:{row.sender.cfg.port}")
        self.refresh(sample=False)

    def _restart_row(self, row: _Row) -> None:
        row.sender.stop()
        try:
            row.sender.start()
            row.meter.reset()
            self.log.emit(f"Started {row.sender.cfg.group}:{row.sender.cfg.port} stream {row.sender.stream_id:08x}")
        except OSError as e:
            self.log.emit(f"Start failed for {row.sender.cfg.group}: {e}")
        self.refresh(sample=False)

    def _toggle(self, row: _Row) -> None:
        if row.sender.running:
            self._stop_row(row)
        else:
            self._restart_row(row)

    def _build_menu(self, menu: QMenu, idxs: list[int]) -> None:
        rows = [self.rows[i] for i in idxs]
        n = len(rows)
        what = "stream" if n == 1 else f"{n} streams"
        menu.addAction(f"Edit settings ({what})…", lambda: self._edit_rows(idxs))
        menu.addSeparator()
        if any(not r.sender.running for r in rows):
            menu.addAction("Start", lambda: [self._restart_row(r) for r in rows if not r.sender.running])
        if any(r.sender.running for r in rows):
            menu.addAction("Stop", lambda: [self._stop_row(r) for r in rows])
        menu.addAction("Restart (new stream)", lambda: [self._restart_row(r) for r in rows])
        menu.addSeparator()
        menu.addAction("Remove", lambda: self._remove_rows(idxs))

    def _edit_selected(self) -> None:
        idxs = self._rows()
        if idxs:
            self._edit_rows(idxs)

    def _edit_rows(self, idxs: list[int]) -> None:
        rows = [self.rows[i] for i in idxs]
        dlg = EditStreamsDialog([r.sender.cfg for r in rows], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = 0
        for r in rows:
            changes = dlg.changes_for(r.sender.cfg)
            if not changes:
                break
            try:
                r.sender.update(replace(r.sender.cfg, **changes))
            except ValueError as e:
                QMessageBox.warning(self, "Cannot apply settings", f"{r.sender.cfg.group}: {e}")
                break
            updated += 1
        if updated:
            names = rows[0].sender.cfg.group if updated == 1 else f"{updated} streams"
            self.log.emit(f"Updated {names}: {dlg.summary()}")
        self.refresh(sample=False)

    def _stop_selected(self) -> None:
        for r in self._rows():
            self._stop_row(self.rows[r])

    def _restart_selected(self) -> None:
        for r in self._rows():
            self._restart_row(self.rows[r])

    def _remove_rows(self, indexes: list[int]) -> None:
        for r in sorted(indexes, reverse=True):
            row = self.rows.pop(r)
            row.sender.stop(wait=False)
            self.graph.remove(row.key)
            self.table.removeRow(r)

    def _remove_selected(self) -> None:
        self._remove_rows(self._rows())

    def _remove_all(self) -> None:
        self.stop_all()
        self._remove_rows(list(range(len(self.rows))))

    def _stop_all_clicked(self) -> None:
        running = [r for r in self.rows if r.sender.running]
        self.stop_all()
        if running:
            self.log.emit(f"Stopped all streams ({len(running)})")
        self.refresh(sample=False)

    def stop_all(self) -> None:
        for row in self.rows:
            row.sender.stop(wait=False)
        for row in self.rows:
            row.sender.stop()

    # ----- periodic refresh ----------------------------------------------------------

    def refresh(self, sample: bool = True) -> None:
        """Update the table. Only the periodic timer samples rates (sample=True);
        refreshes triggered by button clicks would otherwise take a sample a few
        milliseconds after the last one and show a false spike on the graph."""
        t = self.table
        for i, row in enumerate(self.rows):
            s = row.sender
            c = s.cfg
            snap = s.snapshot()
            pps, bps = row.meter.update(snap.packets, snap.bytes) if sample else (row.meter.pps, row.meter.bps)
            if not snap.running:
                pps = bps = 0.0
            if sample:
                self.graph.append(row.key, {"pps": pps, "mbps": bps / 1e6}, active=snap.running)
            if snap.error:
                status = f"Error: {snap.error}"
            elif snap.running:
                status = "Sending"
            else:
                status = "Stopped" if snap.packets else "Idle"
            target = f"{c.rate_pps:,.0f} pps / {c.target_mbps:,.2f} Mbps" if c.rate_pps else "max"
            values = [c.group, str(c.port), row.iface_label, str(c.size), target, fmt_int(snap.packets),
                      f"{pps:,.0f}", f"{bps / 1e6:,.2f}", fmt_int(snap.errors), status]
            for col, text in enumerate(values, start=FIRST_DATA_COL):
                set_cell(t, i, col, text)
            if row.button is not None:
                row.button.setText("Stop" if snap.running else "Start")
        self.graph.redraw()
