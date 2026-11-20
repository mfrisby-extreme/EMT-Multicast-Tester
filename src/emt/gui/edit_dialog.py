"""Bulk "Edit stream settings" dialog for one or more sender streams."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emt.core import SenderConfig
from emt.core.packet import HEADER_SIZE, MAX_UDP_PAYLOAD_V4
from emt.gui.common import wrap_all_tooltips


class EditStreamsDialog(QDialog):
    """Only ticked fields are applied, so several streams with different
    settings can have just one value (e.g. the rate) changed together.
    Editing a value ticks its box automatically."""

    def __init__(self, configs: list[SenderConfig], parent=None) -> None:
        super().__init__(parent)
        first = configs[0]
        n = len(configs)
        self.setWindowTitle("Edit stream" if n == 1 else f"Edit {n} streams")

        self.rate = QDoubleSpinBox(minimum=0, maximum=10_000_000, value=first.rate_pps, decimals=2)
        self.rate.setToolTip("Per stream. 0 = send as fast as possible")
        self.rate_unit = QComboBox()
        self.rate_unit.addItems(["pps", "Mbps"])
        self.rate_unit.currentTextChanged.connect(self._unit_changed)
        rate_box = QWidget()
        rl = QHBoxLayout(rate_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.rate, 1)
        rl.addWidget(self.rate_unit)

        self.size = QSpinBox(minimum=HEADER_SIZE, maximum=MAX_UDP_PAYLOAD_V4, value=first.size, suffix=" bytes")
        self.ttl = QSpinBox(minimum=1, maximum=255, value=first.ttl)
        self.dscp = QSpinBox(minimum=0, maximum=63, value=first.dscp)
        self.count = QSpinBox(minimum=0, maximum=2_000_000_000, value=first.count, specialValueText="unlimited")
        self.count.setToolTip("Total packets for the stream, including those already sent")
        self.duration = QSpinBox(minimum=0, maximum=86_400 * 7, value=int(first.duration), suffix=" s",
                                 specialValueText="unlimited")
        self.duration.setToolTip("Total run time, measured from when the stream started")
        self.loop = QComboBox()
        self.loop.addItems(["On", "Off"])
        self.loop.setCurrentIndex(0 if first.loop else 1)

        self._fields: dict[str, tuple[QCheckBox, QWidget]] = {}
        grid = QGridLayout()
        rows = [
            ("rate", "Rate per stream", rate_box, self.rate.valueChanged),
            ("size", "Packet size", self.size, self.size.valueChanged),
            ("ttl", "TTL", self.ttl, self.ttl.valueChanged),
            ("dscp", "DSCP", self.dscp, self.dscp.valueChanged),
            ("loop", "Loop back to this host", self.loop, self.loop.currentIndexChanged),
            ("count", "Packet count", self.count, self.count.valueChanged),
            ("duration", "Duration", self.duration, self.duration.valueChanged),
        ]
        for i, (key, label, editor, changed) in enumerate(rows):
            box = QCheckBox(label)
            changed.connect(lambda *_, b=box: b.setChecked(True))
            grid.addWidget(box, i, 0)
            grid.addWidget(editor, i, 1)
            self._fields[key] = (box, editor)

        hint = QLabel("Tick the settings to change. Running streams update immediately "
                      "and keep their sequence numbers." if n > 1 else
                      "Tick the settings to change. A running stream updates immediately "
                      "and keeps its sequence numbers.")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(grid)
        layout.addWidget(buttons)
        wrap_all_tooltips(self)

    def _unit_changed(self, unit: str) -> None:
        # Convert the shown value so switching units doesn't change the meaning.
        size = self.size.value()
        v = self.rate.value()
        self.rate.blockSignals(True)
        self.rate.setValue(v * size * 8 / 1e6 if unit == "Mbps" else SenderConfig.pps_from_mbps(v, size))
        self.rate.blockSignals(False)

    def _ticked(self, key: str) -> bool:
        return self._fields[key][0].isChecked()

    def changes_for(self, cfg: SenderConfig) -> dict[str, Any]:
        """The ticked changes, resolved for one stream (Mbps depends on each stream's packet size)."""
        ch: dict[str, Any] = {}
        if self._ticked("size"):
            ch["size"] = self.size.value()
        if self._ticked("rate"):
            size = ch.get("size", cfg.size)
            v = self.rate.value()
            ch["rate_pps"] = SenderConfig.pps_from_mbps(v, size) if self.rate_unit.currentText() == "Mbps" else v
        if self._ticked("ttl"):
            ch["ttl"] = self.ttl.value()
        if self._ticked("dscp"):
            ch["dscp"] = self.dscp.value()
        if self._ticked("loop"):
            ch["loop"] = self.loop.currentIndex() == 0
        if self._ticked("count"):
            ch["count"] = self.count.value()
        if self._ticked("duration"):
            ch["duration"] = float(self.duration.value())
        return ch

    def summary(self) -> str:
        parts = []
        if self._ticked("rate"):
            parts.append(f"rate {self.rate.value():,.2f} {self.rate_unit.currentText()}")
        for key, label, w in (("size", "size", self.size), ("ttl", "TTL", self.ttl), ("dscp", "DSCP", self.dscp),
                              ("count", "count", self.count), ("duration", "duration", self.duration)):
            if self._ticked(key):
                parts.append(f"{label} {w.text()}")
        if self._ticked("loop"):
            parts.append(f"loopback {self.loop.currentText().lower()}")
        return ", ".join(parts)
