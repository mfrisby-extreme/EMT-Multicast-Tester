"""Receiver tab: join groups and watch what arrives."""

from __future__ import annotations

import csv
import itertools
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
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

from emt.core import RateMeter, Receiver, ReceiverConfig
from emt.core.sender import SenderConfig
from emt.core.sockets import IS_WIN, family_for, group_range, is_ssm_range
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
from emt.gui.graph import LOSS_METRICS, RATE_METRICS, RateGraph, stream_color

COLUMNS = ["", "", "Group", "Port", "Source filter", "Interface", "Senders", "Packets", "pps", "Mbps",
           "Lost", "Loss %", "Dup", "Out of order", "Jitter ms", "Join→1st pkt ms", "Status"]
BUTTON_COL, SWATCH_COL, FIRST_DATA_COL = 0, 1, 2
STATUS_COL = COLUMNS.index("Status")


@dataclass
class _Row:
    key: int
    receiver: Receiver
    iface_label: str
    color: QColor
    meter: RateMeter
    loss_t: float | None = None  # last loss sample (monotonic time) and total, for lost pkts/s
    loss_last: int = 0
    button: QPushButton | None = None


class ReceiverTab(QWidget):
    log = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[_Row] = []
        self._keys = itertools.count()
        # Set by the main window: configs of streams this app is currently sending.
        self.local_streams: Callable[[], list[SenderConfig]] = lambda: []

        self.iface = InterfaceCombo()
        self.group = QLineEdit("239.1.1.1")
        self.group_count = QSpinBox(minimum=1, maximum=1000, value=1)
        self.group_count.setToolTip("Join this many consecutive group addresses")
        self.port = QSpinBox(minimum=1, maximum=65535, value=5000)
        self.source = QLineEdit()
        self.source.setPlaceholderText("optional - source IP for SSM (IGMPv3)")

        form = QFormLayout()
        form.addRow("Interface", self.iface)
        form.addRow("First group", self.group)
        form.addRow("Number of groups", self.group_count)
        form.addRow("Port", self.port)
        form.addRow("Source", self.source)
        self.ignore_local = QCheckBox("Ignore traffic from this host")
        self.ignore_local.setChecked(True)
        self.ignore_local.setToolTip(
            "Drop packets sent from this computer's own addresses, so only traffic that came "
            "through the network is counted. Untick to test sender and receiver on one machine "
            "(and tick 'Loop back to this host' on the Send tab).")
        form.addRow("", self.ignore_local)
        join_btn = QPushButton("Join")
        join_btn.setDefault(True)
        join_btn.clicked.connect(self._join)
        form.addRow("", join_btn)

        box = QGroupBox("Join groups")
        box.setLayout(form)

        self.table = make_table(COLUMNS)
        self.table.setColumnWidth(SWATCH_COL, 28)
        self.table.itemChanged.connect(self._item_changed)
        install_context_menu(self.table, self._build_menu)

        btns = QHBoxLayout()
        for label, slot in (
            ("Leave", self._leave_selected),
            ("Rejoin", self._rejoin_selected),
            ("Reset counters", self._reset_selected),
            ("Remove", self._remove_selected),
            ("Leave all", self._leave_all_clicked),
            ("Remove all", self._remove_all),
            ("Export CSV…", self._export_csv),
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

        self.graph = RateGraph({**RATE_METRICS, **LOSS_METRICS})
        self.graph.unit.setToolTip(
            "Lost pkts/s: packets missing from each stream's sequence, per second. A gap is counted "
            "when the next packet arrives, so during a total outage the line shows zero traffic on "
            "the pps/Mbps view and the loss appears as a spike when the stream resumes.")

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
            self.log.emit("Select a group in the table first")
        return rows

    def _find(self, cfg: ReceiverConfig) -> _Row | None:
        """Existing row for the same group + port + interface + source, if any."""
        key = (cfg.group, cfg.port, cfg.iface_ip, cfg.iface_index, cfg.source)
        for row in self.rows:
            c = row.receiver.cfg
            if (c.group, c.port, c.iface_ip, c.iface_index, c.source) == key:
                return row
        return None

    def _join(self) -> None:
        source = self.source.text().strip() or None
        try:
            groups = group_range(self.group.text().strip(), self.group_count.value())
            itf = self.iface.selected()
            ip, idx = itf.params_for(family_for(groups[0])) if itf else (None, 0)
            configs = [ReceiverConfig(g, self.port.value(), ip, idx, source, self.ignore_local.isChecked())
                       for g in groups]
            for c in configs:
                c.validate()
        except ValueError as e:
            QMessageBox.warning(self, "Cannot join groups", str(e))
            return
        new = rejoined = updated = skipped = 0
        for cfg in configs:
            row = self._find(cfg)
            try:
                if row is None:
                    rx = Receiver(cfg)
                    rx.start()
                    self._add_row(rx)
                    new += 1
                    continue
                # Existing row: apply the form's current settings before (re)joining.
                changed = row.receiver.cfg.ignore_local != cfg.ignore_local
                if changed:
                    row.receiver.set_ignore_local(cfg.ignore_local)
                if row.receiver.running:
                    if changed:
                        updated += 1
                    else:
                        skipped += 1  # already joined with these settings
                else:
                    row.receiver.start()
                    rejoined += 1
            except (OSError, NotImplementedError) as e:
                QMessageBox.warning(self, "Cannot join group", f"{cfg.group}: {e}")
                break
        done = new + rejoined + updated + skipped
        if done:
            first = configs[0].group
            if is_ssm_range(first) and not source:
                self.log.emit(f"Warning: {first} is in the SSM range but no source was given")
            groups_txt = first if done == 1 else f"{first} - {configs[done - 1].group} ({done} groups)"
            how = f"source {source}" if source else "any source"
            if not self.ignore_local.isChecked():
                how += ", including this host's traffic"
            parts = [f"{n} {label}" for n, label in ((new, "new"), (rejoined, "rejoined"),
                                                      (updated, "updated"), (skipped, "already joined")) if n]
            self.log.emit(f"Joined {groups_txt} port {self.port.value()} ({how}) on {self.iface.short_label()}"
                          f" - {', '.join(parts)}")
        self.refresh(sample=False)

    def _add_row(self, receiver: Receiver) -> None:
        key = next(self._keys)
        row = _Row(key, receiver, self.iface.short_label(), stream_color(key), RateMeter())
        self.rows.append(row)
        idx = append_row(self.table, select=False)
        self.table.blockSignals(True)
        self.table.setItem(idx, SWATCH_COL, swatch_item(row.color))
        self.table.blockSignals(False)
        row.button = QPushButton("Leave")
        row.button.setToolTip(wrap_tooltip("Leave / rejoin this group"))
        row.button.clicked.connect(lambda _=False, r=row: self._toggle(r))
        self.table.setCellWidget(idx, BUTTON_COL, row.button)
        self.graph.add(key, row.color)
        self.table.selectRow(idx)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == SWATCH_COL and item.row() < len(self.rows):
            self.graph.set_visible(self.rows[item.row()].key, item.checkState() == Qt.CheckState.Checked)

    def _leave_row(self, row: _Row) -> None:
        rx = row.receiver
        if rx.running:
            rx.stop()
            self.log.emit(f"Left {rx.cfg.group}:{rx.cfg.port}")
        self.refresh(sample=False)

    def _rejoin_row(self, row: _Row) -> None:
        rx = row.receiver
        try:
            rx.rejoin()
            self.log.emit(f"Rejoined {rx.cfg.group}:{rx.cfg.port}")
        except OSError as e:
            self.log.emit(f"Rejoin failed for {rx.cfg.group}: {e}")
        self.refresh(sample=False)

    def _toggle(self, row: _Row) -> None:
        if row.receiver.running:
            self._leave_row(row)
        else:
            self._rejoin_row(row)

    def _build_menu(self, menu: QMenu, idxs: list[int]) -> None:
        rows = [self.rows[i] for i in idxs]
        if any(not r.receiver.running for r in rows):
            menu.addAction("Join", lambda: [self._rejoin_row(r) for r in rows if not r.receiver.running])
        if any(r.receiver.running for r in rows):
            menu.addAction("Leave", lambda: [self._leave_row(r) for r in rows])
        menu.addAction("Rejoin (leave + join)", lambda: [self._rejoin_row(r) for r in rows])
        menu.addSeparator()
        ignore = menu.addAction("Ignore traffic from this host")
        ignore.setCheckable(True)
        ignore.setChecked(all(r.receiver.cfg.ignore_local for r in rows))
        ignore.toggled.connect(lambda on: self._set_ignore_local(rows, on))
        menu.addSeparator()
        menu.addAction("Reset counters", self._reset_selected)
        menu.addAction("Remove", lambda: self._remove_rows(idxs))

    def _status(self, c: ReceiverConfig, snap) -> str:
        """snap.status, plus a hint when this app's own sender can't reach this receiver.

        On macOS/Linux a sender with loopback off never delivers to its own host, so a
        same-machine test shows nothing however the receiver is set. (On Windows
        loopback is controlled on the receiving side, so the hint doesn't apply.)
        """
        status = snap.status
        if snap.running and not snap.error and not snap.packets and not c.ignore_local and not IS_WIN:
            if any(s.group == c.group and s.port == c.port and not s.loop for s in self.local_streams()):
                return "Joined, waiting - this app's sender has 'Loop back to this host' off"
        return status

    def _set_ignore_local(self, rows: list[_Row], ignore: bool) -> None:
        for r in rows:
            r.receiver.set_ignore_local(ignore)
        what = rows[0].receiver.cfg.group if len(rows) == 1 else f"{len(rows)} groups"
        self.log.emit(f"{what}: {'ignoring' if ignore else 'counting'} traffic from this host")
        self.refresh(sample=False)

    def _leave_selected(self) -> None:
        for r in self._rows():
            self._leave_row(self.rows[r])

    def _rejoin_selected(self) -> None:
        for r in self._rows():
            self._rejoin_row(self.rows[r])

    def _reset_selected(self) -> None:
        for r in self._rows():
            row = self.rows[r]
            row.receiver.reset()
            row.meter.reset()
            row.loss_t, row.loss_last = None, 0
            self.graph.clear(row.key)

    def _remove_rows(self, indexes: list[int]) -> None:
        for r in sorted(indexes, reverse=True):
            row = self.rows.pop(r)
            row.receiver.stop()
            self.graph.remove(row.key)
            self.table.removeRow(r)

    def _remove_selected(self) -> None:
        self._remove_rows(self._rows())

    def _remove_all(self) -> None:
        self._remove_rows(list(range(len(self.rows))))

    def _leave_all_clicked(self) -> None:
        for row in self.rows:
            self._leave_row(row)

    def leave_all(self) -> None:
        for row in self.rows:
            row.receiver.stop()

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export receiver stats", f"emt-receive-{time.strftime('%Y%m%d-%H%M%S')}.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["group", "port", "source_filter", "interface", "sender", "stream_id", "packets", "bytes",
                        "expected", "lost", "loss_pct", "duplicates", "out_of_order", "jitter_ms"])
            for row in self.rows:
                rx = row.receiver
                for s in rx.snapshot().streams:
                    w.writerow([rx.cfg.group, rx.cfg.port, rx.cfg.source or "", row.iface_label, s.source,
                                "raw" if s.is_raw else f"{s.stream_id:08x}", s.packets, s.bytes, s.expected,
                                s.lost, f"{s.loss_pct:.3f}", s.duplicates, s.out_of_order, f"{s.jitter_ms:.3f}"])
        self.log.emit(f"Exported receiver stats to {path}")

    # ----- periodic refresh ----------------------------------------------------------

    def refresh(self, sample: bool = True) -> None:
        """Update the table. Only the periodic timer samples rates (sample=True);
        refreshes triggered by button clicks would otherwise take a sample a few
        milliseconds after the last one and show a false spike on the graph."""
        t = self.table
        for i, row in enumerate(self.rows):
            rx = row.receiver
            c = rx.cfg
            snap = rx.snapshot()
            pps, bps = row.meter.update(snap.packets, snap.bytes) if sample else (row.meter.pps, row.meter.bps)
            if sample:
                now = time.monotonic()
                lost = snap.lost
                lost_ps = (max(0, lost - row.loss_last) / (now - row.loss_t)
                           if row.loss_t is not None and now > row.loss_t else 0.0)
                row.loss_t, row.loss_last = now, lost
                self.graph.append(row.key, {"pps": pps, "mbps": bps / 1e6, "lost_ps": lost_ps,
                                            "lost_total": float(lost)}, active=snap.running)
            first = snap.join_to_first_ms
            status = self._status(c, snap)
            values = [
                c.group, str(c.port), (c.source or "any") + ("" if c.ignore_local else " + this host"), row.iface_label, ", ".join(snap.senders) or "-",
                fmt_int(snap.packets), f"{pps:,.0f}", f"{bps / 1e6:,.2f}", fmt_int(snap.lost),
                f"{snap.loss_pct:.2f}", fmt_int(snap.duplicates), fmt_int(snap.out_of_order),
                f"{snap.jitter_ms:.2f}", f"{first:,.1f}" if first is not None else "-", status,
            ]
            for col, text in enumerate(values, start=FIRST_DATA_COL):
                set_cell(t, i, col, text)
            if row.button is not None:
                row.button.setText("Leave" if snap.running else "Join")
            color = (QColor("#2e7d32") if status == "Receiving"
                     else QColor("#c62828") if status.startswith(("Error", "Stalled"))
                     else QColor("#9e6a00") if status.startswith("Joined") else None)
            item = t.item(i, STATUS_COL)
            if item is not None:
                item.setForeground(color if color is not None else t.palette().text().color())
        self.graph.redraw()
