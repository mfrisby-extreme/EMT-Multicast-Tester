"""Per-stream receive statistics and rate measurement."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# How many recent sequence numbers to remember for duplicate detection.
DUP_WINDOW = 8192


@dataclass(frozen=True)
class StreamSnapshot:
    """Immutable view of one stream's counters, safe to hand to another thread."""

    source: str
    stream_id: int | None  # None = non-EMT ("raw") traffic from this source
    packets: int
    bytes: int
    expected: int
    lost: int
    duplicates: int
    out_of_order: int
    jitter_ms: float
    first_rx_ns: int
    last_rx_ns: int

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.lost / self.expected if self.expected else 0.0

    @property
    def is_raw(self) -> bool:
        return self.stream_id is None


@dataclass
class StreamStats:
    """Mutable counters for one (source, stream_id). Only touched by the receiver thread."""

    source: str
    stream_id: int | None
    packets: int = 0
    bytes: int = 0
    first_seq: int | None = None
    max_seq: int = -1
    duplicates: int = 0
    out_of_order: int = 0
    jitter_ns: float = 0.0
    first_rx_ns: int = 0
    last_rx_ns: int = 0
    _prev_transit: int | None = field(default=None, repr=False)
    _seen: set[int] = field(default_factory=set, repr=False)

    def _touch(self, size: int, rx_ns: int) -> None:
        self.packets += 1
        self.bytes += size
        if not self.first_rx_ns:
            self.first_rx_ns = rx_ns
        self.last_rx_ns = rx_ns

    def record_raw(self, size: int, rx_ns: int) -> None:
        self._touch(size, rx_ns)

    def record(self, seq: int, send_ns: int, rx_ns: int, size: int) -> None:
        self._touch(size, rx_ns)

        if seq in self._seen:
            self.duplicates += 1
            return

        if self.first_seq is None:
            self.first_seq = seq
            self.max_seq = seq
        elif seq > self.max_seq:
            self.max_seq = seq
        else:
            # Arrived after a higher sequence number: reordered (or very late).
            self.out_of_order += 1
            if seq < self.first_seq:
                self.first_seq = seq

        self._seen.add(seq)
        if len(self._seen) > 2 * DUP_WINDOW:
            floor = self.max_seq - DUP_WINDOW
            self._seen = {s for s in self._seen if s >= floor}

        # RFC 3550 interarrival jitter. Uses the *difference* in transit time,
        # so sender/receiver clocks do not need to be synchronised.
        transit = rx_ns - send_ns
        if self._prev_transit is not None:
            d = abs(transit - self._prev_transit)
            self.jitter_ns += (d - self.jitter_ns) / 16.0
        self._prev_transit = transit

    @property
    def expected(self) -> int:
        return 0 if self.first_seq is None else self.max_seq - self.first_seq + 1

    @property
    def lost(self) -> int:
        unique = self.packets - self.duplicates
        return max(0, self.expected - unique)

    def snapshot(self) -> StreamSnapshot:
        return StreamSnapshot(
            source=self.source,
            stream_id=self.stream_id,
            packets=self.packets,
            bytes=self.bytes,
            expected=self.expected if self.stream_id is not None else self.packets,
            lost=self.lost if self.stream_id is not None else 0,
            duplicates=self.duplicates,
            out_of_order=self.out_of_order,
            jitter_ms=self.jitter_ns / 1e6,
            first_rx_ns=self.first_rx_ns,
            last_rx_ns=self.last_rx_ns,
        )


class RateMeter:
    """Turns ever-increasing packet/byte counters into pps and bits/s between polls."""

    def __init__(self) -> None:
        self._t: float | None = None
        self._packets = 0
        self._bytes = 0
        self.pps = 0.0
        self.bps = 0.0

    def update(self, packets: int, nbytes: int, now: float | None = None) -> tuple[float, float]:
        now = time.monotonic() if now is None else now
        if self._t is not None and now > self._t:
            dt = now - self._t
            self.pps = max(0.0, (packets - self._packets) / dt)
            self.bps = max(0.0, (nbytes - self._bytes) * 8 / dt)
        self._t, self._packets, self._bytes = now, packets, nbytes
        return self.pps, self.bps

    def reset(self) -> None:
        self.__init__()
