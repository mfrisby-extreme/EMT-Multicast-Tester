import pytest

from emt.core import packet


def test_roundtrip():
    buf = packet.make_buffer(100)
    packet.stamp(buf, stream_id=0xDEADBEEF, seq=42, send_ns=123456789)
    hdr = packet.parse(buf)
    assert hdr == packet.Header(0xDEADBEEF, 42, 123456789)
    assert len(buf) == 100


def test_non_emt_payload_is_none():
    assert packet.parse(b"x" * 100) is None
    assert packet.parse(b"EMT1") is None  # too short


def test_min_size():
    with pytest.raises(ValueError):
        packet.make_buffer(packet.HEADER_SIZE - 1)


def test_group_range():
    from emt.core.sockets import group_range

    assert group_range("234.5.6.7", 5) == ["234.5.6.7", "234.5.6.8", "234.5.6.9", "234.5.6.10", "234.5.6.11"]
    assert group_range("239.1.1.255", 2) == ["239.1.1.255", "239.1.2.0"]
    assert group_range("ff15::1", 2) == ["ff15::1", "ff15::2"]
    with pytest.raises(ValueError):
        group_range("239.255.255.255", 2)
    with pytest.raises(ValueError):
        group_range("10.0.0.1", 1)
