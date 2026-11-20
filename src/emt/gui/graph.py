"""Live per-stream graph (one coloured line per stream, selectable metric)."""

from __future__ import annotations

import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

NAN = float("nan")
HISTORY = 1200  # samples kept per stream (10 min at the 0.5 s refresh)
WINDOWS = {"30 s": 30, "1 min": 60, "2 min": 120, "5 min": 300, "10 min": 600}

# metric key -> label shown in the drop-down and on the y axis
RATE_METRICS = {"mbps": "Mbps", "pps": "pps"}
LOSS_METRICS = {"lost_ps": "Lost pkts/s", "lost_total": "Lost pkts (total)"}


def stream_color(i: int) -> QColor:
    """Distinct, evenly spread colours (golden-ratio hue steps)."""
    return QColor.fromHsvF((0.02 + i * 0.618034) % 1.0, 0.8, 0.85)


class _Series:
    def __init__(self, curve: pg.PlotDataItem, metrics: list[str]) -> None:
        self.curve = curve
        self.t: deque[float] = deque(maxlen=HISTORY)
        self.values: dict[str, deque[float]] = {m: deque(maxlen=HISTORY) for m in metrics}
        self.active = False


class RateGraph(QWidget):
    def __init__(self, metrics: dict[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.metrics = dict(metrics or RATE_METRICS)
        pg.setConfigOptions(antialias=True)
        pal = self.palette()
        fg = pal.color(QPalette.ColorRole.Text)

        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setBackground(pal.color(QPalette.ColorRole.Base))
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        for side in ("left", "bottom"):
            ax = self.plot.getAxis(side)
            ax.setPen(fg)
            ax.setTextPen(fg)
        self.plot.enableAutoRange(axis="y")
        self.plot.setLimits(yMin=0)

        self.unit = QComboBox()
        for key, label in self.metrics.items():
            self.unit.addItem(label, key)
        self.unit.currentTextChanged.connect(self.redraw)
        self.window = QComboBox()
        self.window.addItems(list(WINDOWS))
        self.window.setCurrentText("2 min")
        self.window.currentTextChanged.connect(self.redraw)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Graph:"))
        bar.addWidget(self.unit)
        bar.addWidget(QLabel("Window:"))
        bar.addWidget(self.window)
        clear_btn = QPushButton("Clear")
        clear_btn.setToolTip("Clear the graph history (counters in the table are not reset)")
        clear_btn.clicked.connect(self.clear_all)
        bar.addWidget(clear_btn)
        bar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.plot, 1)

        self.series: dict[int, _Series] = {}
        self._frozen_at: float | None = None  # end of the x axis while nothing is active

    def set_metric(self, key: str) -> None:
        i = self.unit.findData(key)
        if i >= 0:
            self.unit.setCurrentIndex(i)

    @property
    def metric(self) -> str:
        return self.unit.currentData()

    def add(self, key: int, color: QColor) -> None:
        curve = self.plot.plot(pen=pg.mkPen(color, width=2))
        self.series[key] = _Series(curve, list(self.metrics))

    def remove(self, key: int) -> None:
        s = self.series.pop(key, None)
        if s is not None:
            self.plot.removeItem(s.curve)

    def set_visible(self, key: int, visible: bool) -> None:
        if key in self.series:
            self.series[key].curve.setVisible(visible)

    def clear(self, key: int) -> None:
        s = self.series.get(key)
        if s is not None:
            s.t.clear()
            for d in s.values.values():
                d.clear()

    def clear_all(self) -> None:
        """Wipe every line; running streams start drawing again from now."""
        for key in self.series:
            self.clear(key)
        self._frozen_at = None
        self.redraw()

    def _push(self, s: _Series, t: float, values: dict[str, float] | None) -> None:
        s.t.append(t)
        for m, d in s.values.items():
            d.append(NAN if values is None else values.get(m, 0.0))

    def append(self, key: int, values: dict[str, float], active: bool = True, t: float | None = None) -> None:
        """Add a sample (metric key -> value). While a stream is inactive (stopped / left)
        no points are added: its line drops to zero once and then ends, and a NaN break is
        inserted when it becomes active again so the gap isn't drawn as a ramp."""
        s = self.series.get(key)
        if s is None:
            return
        t = time.time() if t is None else t
        if active:
            if not s.active and s.t:
                self._push(s, t, None)
            self._push(s, t, values)
        elif s.active:
            # Rates fall to zero; a running total keeps its last value.
            self._push(s, t, {m: (values.get(m, 0.0) if m == "lost_total" else 0.0) for m in s.values})
        s.active = active

    def redraw(self) -> None:
        metric = self.metric
        for s in self.series.values():
            s.curve.setData(list(s.t), list(s.values[metric]), connect="finite")
        # Scroll only while something is running; otherwise hold the view where it stopped.
        if any(s.active for s in self.series.values()):
            self._frozen_at = None
            end = time.time()
        else:
            if self._frozen_at is None:
                self._frozen_at = max((s.t[-1] for s in self.series.values() if s.t), default=time.time())
            end = self._frozen_at
        self.plot.setXRange(end - WINDOWS[self.window.currentText()], end, padding=0)
        self.plot.setLabel("left", self.unit.currentText())
