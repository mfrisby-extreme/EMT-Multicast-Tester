from emt.core.stats import RateMeter, StreamStats


def feed(seqs):
    st = StreamStats("10.0.0.1", 1)
    for i, s in enumerate(seqs):
        st.record(s, send_ns=s * 1_000_000, rx_ns=s * 1_000_000 + 500_000, size=100)
    return st


def test_clean_stream():
    st = feed(range(100))
    assert (st.expected, st.lost, st.duplicates, st.out_of_order) == (100, 0, 0, 0)
    assert st.jitter_ns == 0


def test_loss():
    st = feed([0, 1, 2, 5, 6, 9])
    assert st.expected == 10
    assert st.lost == 4


def test_duplicates_and_reorder():
    st = feed([0, 1, 3, 2, 3, 4])
    assert st.duplicates == 1
    assert st.out_of_order == 1
    assert st.lost == 0


def test_joined_mid_stream():
    st = feed([500, 501, 502])
    assert st.expected == 3
    assert st.lost == 0


def test_snapshot_loss_pct():
    snap = feed([0, 1, 2, 3, 5, 6, 7, 8, 9]).snapshot()
    assert snap.lost == 1
    assert round(snap.loss_pct, 1) == 10.0


def test_rate_meter():
    m = RateMeter()
    m.update(0, 0, now=0.0)
    pps, bps = m.update(100, 100_000, now=1.0)
    assert pps == 100
    assert bps == 800_000
