"""Multicast receiver: joins a group and measures what arrives."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, replace

from emt.core import packet
from emt.core.netif import local_addresses
from emt.core.sockets import family_for, open_receiver
from emt.core.stats import StreamSnapshot, StreamStats

# A group that was receiving but has seen nothing for this long is "stalled".
STALL_S = 2.0


@dataclass
class ReceiverConfig:
    group: str
    port: int
    iface_ip: str | None = None
    iface_index: int = 0
    source: str | None = None  # set for a source-specific (SSM / IGMPv3) join
    # Drop packets sent by this host, so a sender and receiver on the same machine
    # only see traffic that really came back from the network. Done in software
    # because IP_MULTICAST_LOOP is sender-side on macOS/Linux but receiver-side on Windows.
    ignore_local: bool = True

    def validate(self) -> None:
        family_for(self.group)
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be 1-65535")
        if self.source:
            socket.inet_pton(socket.AF_INET if ":" not in self.source else socket.AF_INET6, self.source)


@dataclass(frozen=True)
class ReceiverSnapshot:
    running: bool
    error: str | None
    join_ns: int
    first_packet_ns: int
    streams: tuple[StreamSnapshot, ...]
    ignored_local: int = 0

    @property
    def packets(self) -> int:
        return sum(s.packets for s in self.streams)

    @property
    def bytes(self) -> int:
        return sum(s.bytes for s in self.streams)

    @property
    def expected(self) -> int:
        return sum(s.expected for s in self.streams)

    @property
    def lost(self) -> int:
        return sum(s.lost for s in self.streams)

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.lost / self.expected if self.expected else 0.0

    @property
    def duplicates(self) -> int:
        return sum(s.duplicates for s in self.streams)

    @property
    def out_of_order(self) -> int:
        return sum(s.out_of_order for s in self.streams)

    @property
    def jitter_ms(self) -> float:
        return max((s.jitter_ms for s in self.streams if not s.is_raw), default=0.0)

    @property
    def senders(self) -> list[str]:
        return sorted({s.source for s in self.streams})

    @property
    def join_to_first_ms(self) -> float | None:
        """Time from IGMP join to first packet - shows how fast the network grafted the flow."""
        if not self.first_packet_ns or not self.join_ns:
            return None
        return (self.first_packet_ns - self.join_ns) / 1e6

    @property
    def idle_s(self) -> float | None:
        last = max((s.last_rx_ns for s in self.streams), default=0)
        return (time.time_ns() - last) / 1e9 if last else None

    @property
    def status(self) -> str:
        if self.error:
            return f"Error: {self.error}"
        if not self.running:
            return "Left"
        idle = self.idle_s
        if idle is None:
            return "Joined, waiting (only local traffic, ignored)" if self.ignored_local else "Joined, waiting"
        if idle > STALL_S:
            return f"Stalled {idle:.0f}s"
        return "Receiving"


class Receiver:
    def __init__(self, cfg: ReceiverConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self.error: str | None = None
        self.join_ns = 0
        self.first_packet_ns = 0
        self.ignored_local = 0
        self._local: set[str] = set()  # source addresses to drop (this host's), if ignore_local
        self._streams: dict[tuple[str, int | None], StreamStats] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Join the group (raises OSError / ValueError if the OS refuses)."""
        if self.running:
            return
        c = self.cfg
        self._local = local_addresses() if c.ignore_local else set()
        sock, leave = open_receiver(c.group, c.port, c.iface_ip, c.iface_index, c.source)
        self.error = None
        self._stop.clear()
        self.join_ns = time.time_ns()
        self.first_packet_ns = 0
        self._thread = threading.Thread(
            target=self._run, args=(sock, leave), name=f"recv-{c.group}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Leave the group."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def set_ignore_local(self, ignore: bool) -> None:
        """Turn the 'ignore traffic from this host' filter on/off; takes effect immediately."""
        self.cfg = replace(self.cfg, ignore_local=ignore)
        self._local = local_addresses() if ignore else set()

    def rejoin(self) -> None:
        self.stop()
        self.start()

    def reset(self) -> None:
        with self._lock:
            self._streams.clear()
            self.first_packet_ns = 0
            self.ignored_local = 0
            self.join_ns = time.time_ns() if self.running else 0

    def snapshot(self) -> ReceiverSnapshot:
        with self._lock:
            streams = tuple(s.snapshot() for s in self._streams.values())
        return ReceiverSnapshot(self.running, self.error, self.join_ns, self.first_packet_ns, streams,
                                self.ignored_local)

    def _run(self, sock: socket.socket, leave) -> None:
        buf = bytearray(65535)
        view = memoryview(buf)
        sock.settimeout(0.25)
        streams = self._streams
        lock = self._lock
        src_filter = self.cfg.source
        try:
            while not self._stop.is_set():
                try:
                    n, addr = sock.recvfrom_into(buf)
                except socket.timeout:
                    continue
                except OSError as e:
                    if not self._stop.is_set():
                        self.error = str(e)
                    break
                rx_ns = time.time_ns()
                src = addr[0].split("%", 1)[0]  # strip IPv6 scope ("fe80::1%en0")
                if src in self._local:
                    self.ignored_local += 1
                    continue
                if src_filter and src != src_filter:
                    continue  # belt and braces; the kernel should already filter SSM
                hdr = packet.parse(view[:n])
                key = (src, hdr.stream_id if hdr else None)
                with lock:
                    if not self.first_packet_ns:
                        self.first_packet_ns = rx_ns
                    st = streams.get(key)
                    if st is None:
                        st = streams[key] = StreamStats(src, key[1])
                    if hdr:
                        st.record(hdr.seq, hdr.send_ns, rx_ns, n)
                    else:
                        st.record_raw(n, rx_ns)
        finally:
            leave()
            sock.close()
