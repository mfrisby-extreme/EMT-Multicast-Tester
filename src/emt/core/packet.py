"""Test packet format.

Every packet the sender emits starts with a fixed 24-byte header so the
receiver can measure loss, duplicates, reordering and jitter:

    offset  size  field
    0       4     magic  b"EMT1"
    4       4     stream_id   (random per sender start, so restarts are separate streams)
    8       8     sequence number (starts at 0)
    16      8     sender wall-clock time, ns since epoch

All fields are network byte order. The rest of the payload is filler.
Packets without the magic are still counted by the receiver as "raw"
traffic (e.g. VLC or iperf streams), just without sequence analysis.
"""

from __future__ import annotations

import struct
import time
from typing import NamedTuple

MAGIC = b"EMT1"
HEADER = struct.Struct("!4sIQQ")
HEADER_SIZE = HEADER.size  # 24
MAX_UDP_PAYLOAD_V4 = 65507
MAX_UDP_PAYLOAD_V6 = 65527


class Header(NamedTuple):
    stream_id: int
    seq: int
    send_ns: int


def make_buffer(size: int) -> bytearray:
    """Allocate a packet buffer of `size` bytes with a recognisable filler pattern."""
    if size < HEADER_SIZE:
        raise ValueError(f"packet size must be at least {HEADER_SIZE} bytes")
    pattern = bytes(range(256))
    reps = size // len(pattern) + 1
    return bytearray((pattern * reps)[:size])


def stamp(buf: bytearray, stream_id: int, seq: int, send_ns: int | None = None) -> None:
    """Write the header into the front of `buf` in place."""
    HEADER.pack_into(buf, 0, MAGIC, stream_id, seq, time.time_ns() if send_ns is None else send_ns)


def parse(data: bytes | bytearray | memoryview) -> Header | None:
    """Return the header if `data` is an EMT packet, else None."""
    if len(data) < HEADER_SIZE:
        return None
    magic, stream_id, seq, send_ns = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        return None
    return Header(stream_id, seq, send_ns)
