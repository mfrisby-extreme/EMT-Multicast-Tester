"""End-to-end send/receive on this host via multicast loopback.

These tests need loopback on in the sender and `ignore_local=False` in the
receiver, because sender and receiver are the same machine.

Skipped (not failed) when the machine has no multicast route, e.g. some CI
containers - the unit tests still cover the accounting logic.
"""

import random
import time

import pytest

from emt.core import Receiver, ReceiverConfig, Sender, SenderConfig
from emt.core.netif import list_interfaces


def _pick_iface():
    for itf in list_interfaces():
        if itf.ipv4 and not itf.is_loopback:
            return itf.ipv4_addr
    return None


def test_send_and_receive():
    group = f"239.255.{random.randint(1, 254)}.{random.randint(1, 254)}"
    port = random.randint(20000, 60000)
    iface = _pick_iface()

    rx = Receiver(ReceiverConfig(group, port, iface_ip=iface, ignore_local=False))
    try:
        rx.start()
    except OSError as e:
        pytest.skip(f"cannot join multicast here: {e}")

    tx = Sender(SenderConfig(group, port, iface_ip=iface, rate_pps=500, count=200, size=200, ttl=1, loop=True))
    try:
        tx.start()
    except OSError as e:
        rx.stop()
        pytest.skip(f"cannot send multicast here: {e}")

    tx.wait(timeout=5)
    time.sleep(0.3)
    rx.stop()

    snap = rx.snapshot()
    if snap.packets == 0:
        pytest.skip("no multicast loopback on this host")
    assert tx.snapshot().packets == 200
    assert snap.packets == 200
    assert snap.lost == 0
    assert snap.duplicates == 0
    assert snap.join_to_first_ms is not None
    assert len(snap.streams) == 1 and snap.streams[0].stream_id == tx.stream_id


def test_live_rate_change_keeps_stream():
    group = f"239.255.{random.randint(1, 254)}.{random.randint(1, 254)}"
    port = random.randint(20000, 60000)
    iface = _pick_iface()
    rx = Receiver(ReceiverConfig(group, port, iface_ip=iface, ignore_local=False))
    try:
        rx.start()
    except OSError as e:
        pytest.skip(f"cannot join multicast here: {e}")
    tx = Sender(SenderConfig(group, port, iface_ip=iface, rate_pps=100, size=200, ttl=1, loop=True))
    tx.start()
    time.sleep(1.0)
    before = tx.snapshot().packets
    from dataclasses import replace

    tx.update(replace(tx.cfg, rate_pps=1000, size=300, ttl=2))
    time.sleep(1.0)
    after = tx.snapshot().packets - before
    tx.stop()
    time.sleep(0.3)
    rx.stop()
    snap = rx.snapshot()
    if snap.packets == 0:
        pytest.skip("no multicast loopback on this host")
    assert 800 <= after <= 1100, after  # ~1000 pps after the change, no catch-up burst
    assert len(snap.streams) == 1  # same stream id throughout
    assert snap.lost == 0
    with pytest.raises(ValueError):
        tx.update(replace(tx.cfg, port=port + 1))


def test_local_traffic_ignored_by_default():
    group = f"239.255.{random.randint(1, 254)}.{random.randint(1, 254)}"
    port = random.randint(20000, 60000)
    iface = _pick_iface()
    rx = Receiver(ReceiverConfig(group, port, iface_ip=iface))  # ignore_local defaults to True
    try:
        rx.start()
    except OSError as e:
        pytest.skip(f"cannot join multicast here: {e}")
    tx = Sender(SenderConfig(group, port, iface_ip=iface, rate_pps=500, count=100, size=100, ttl=1, loop=True))
    tx.start()
    tx.wait(timeout=5)
    time.sleep(0.3)
    rx.stop()
    snap = rx.snapshot()
    if snap.ignored_local == 0:
        pytest.skip("no multicast loopback on this host")
    assert snap.packets == 0
    assert snap.ignored_local == 100
