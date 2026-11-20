"""Multicast traffic generator."""

from __future__ import annotations

import errno
import random
import threading
import time
from dataclasses import dataclass

from emt.core import packet
from emt.core.sockets import apply_sender_options, family_for, open_sender

# If the sender falls this far behind schedule (e.g. laptop slept), stop
# trying to catch up instead of emitting a huge burst.
MAX_CATCHUP_S = 0.5


@dataclass
class SenderConfig:
    group: str
    port: int
    iface_ip: str | None = None
    iface_index: int = 0
    ttl: int = 16
    # Deliver a copy to this host too? Off by default: with it on, a receiver on the same
    # machine shows the stream even when the network isn't forwarding it.
    loop: bool = False
    dscp: int = 0
    size: int = 1316  # UDP payload bytes (7 x 188-byte MPEG-TS cells)
    rate_pps: float = 100.0  # 0 = as fast as possible
    count: int = 0  # 0 = unlimited
    duration: float = 0.0  # seconds, 0 = unlimited

    @staticmethod
    def pps_from_mbps(mbps: float, size: int) -> float:
        return mbps * 1e6 / (size * 8)

    @property
    def target_mbps(self) -> float:
        return self.rate_pps * self.size * 8 / 1e6

    def validate(self) -> None:
        family_for(self.group)
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be 1-65535")
        if not 1 <= self.ttl <= 255:
            raise ValueError("TTL must be 1-255")
        if not 0 <= self.dscp <= 63:
            raise ValueError("DSCP must be 0-63")
        limit = packet.MAX_UDP_PAYLOAD_V4 if ":" not in self.group else packet.MAX_UDP_PAYLOAD_V6
        if not packet.HEADER_SIZE <= self.size <= limit:
            raise ValueError(f"packet size must be {packet.HEADER_SIZE}-{limit} bytes")
        if self.rate_pps < 0:
            raise ValueError("rate must be >= 0")


@dataclass(frozen=True)
class SenderSnapshot:
    packets: int
    bytes: int
    errors: int
    elapsed: float
    running: bool
    error: str | None


class Sender:
    """Sends one paced stream on a background thread.

    The socket is opened in start() so configuration errors surface
    immediately to the caller rather than silently in the thread.
    """

    def __init__(self, cfg: SenderConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self.stream_id = 0
        self.packets = 0
        self.bytes = 0
        self.errors = 0
        self.error: str | None = None
        self._version = 0  # bumped by update(); the send thread reloads cfg when it changes
        self._started = 0.0
        self._ended = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        c = self.cfg
        sock, dest = open_sender(c.group, c.port, c.iface_ip, c.iface_index, c.ttl, c.loop, c.dscp)
        self.stream_id = random.getrandbits(32)
        self.packets = self.bytes = self.errors = 0
        self.error = None
        self._stop.clear()
        self._started = time.monotonic()
        self._ended = 0.0
        self._thread = threading.Thread(target=self._run, args=(sock, dest), name=f"send-{c.group}", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=2.0)

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def update(self, cfg: SenderConfig) -> None:
        """Change rate, size, TTL, DSCP, loopback, count or duration - live if running.

        The stream keeps its stream id and sequence numbers, so receivers see
        one continuous stream. Group, port and interface identify the stream
        and cannot be changed.
        """
        cfg.validate()
        old = self.cfg
        if (cfg.group, cfg.port, cfg.iface_ip, cfg.iface_index) != (old.group, old.port, old.iface_ip, old.iface_index):
            raise ValueError("group, port and interface can't be changed on an existing stream")
        self.cfg = cfg
        self._version += 1

    def snapshot(self) -> SenderSnapshot:
        end = self._ended or time.monotonic()
        elapsed = end - self._started if self._started else 0.0
        return SenderSnapshot(self.packets, self.bytes, self.errors, elapsed, self.running, self.error)

    def _run(self, sock, dest) -> None:
        stop = self._stop
        perf = time.perf_counter
        t0 = start = perf()
        seq = 0
        version = -1
        c = self.cfg
        buf = packet.make_buffer(c.size)
        interval = 0.0
        try:
            while not stop.is_set():
                if self._version != version:  # first pass, or settings changed via update()
                    first = version == -1
                    version = self._version
                    old, c = c, self.cfg
                    if len(buf) != c.size:
                        buf = packet.make_buffer(c.size)
                    if not first and (old.ttl, old.loop, old.dscp) != (c.ttl, c.loop, c.dscp):
                        apply_sender_options(sock, c.ttl, c.loop, c.dscp)
                    interval = 1.0 / c.rate_pps if c.rate_pps > 0 else 0.0
                    # Re-base the schedule so a new rate applies from now, without a catch-up burst.
                    start = perf() - seq * interval
                if c.count and seq >= c.count:
                    break
                now = perf()
                if c.duration and now - t0 >= c.duration:
                    break
                if interval:
                    target = start + seq * interval
                    if now < target:
                        # Sleep in short chunks so stop() and update() stay responsive.
                        time.sleep(min(target - now, 0.05))
                        continue
                    if now - target > MAX_CATCHUP_S:
                        start = now - seq * interval

                packet.stamp(buf, self.stream_id, seq)
                try:
                    sock.sendto(buf, dest)
                except OSError as e:
                    self.errors += 1
                    if e.errno in (errno.ENOBUFS, errno.EAGAIN, getattr(errno, "WSAENOBUFS", -1)):
                        time.sleep(0.001)
                        continue
                    if self.errors >= 100 and self.packets == 0:
                        raise
                    continue
                seq += 1
                self.packets = seq
                self.bytes += c.size
        except Exception as e:  # surfaced via snapshot().error
            self.error = str(e)
        finally:
            self._ended = time.monotonic()
            sock.close()
