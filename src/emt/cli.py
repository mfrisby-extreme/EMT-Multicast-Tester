"""Command-line interface: `emt-cli ifaces | send | recv`."""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import threading
import time

from emt import APP_NAME, __version__
from emt.core import (
    Receiver,
    ReceiverConfig,
    RateMeter,
    Sender,
    SenderConfig,
    list_interfaces,
    resolve_interface,
)
from emt.core.sockets import family_for

COMMANDS = ("ifaces", "send", "recv")


def _iface_params(spec: str | None, group: str) -> tuple[str | None, int]:
    itf = resolve_interface(spec)
    return itf.params_for(family_for(group)) if itf else (None, 0)


def _install_stop_handler() -> threading.Event:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    return stop


def cmd_ifaces(_args) -> int:
    for itf in list_interfaces():
        print(f"{itf.nice_name}  [{itf.name}, index {itf.index}]")
        for a in itf.ipv4:
            print(f"    IPv4  {a}")
        for a in itf.ipv6:
            print(f"    IPv6  {a}")
    return 0


def cmd_send(args) -> int:
    senders: list[Sender] = []
    for group in args.group:
        ip, idx = _iface_params(args.iface, group)
        pps = SenderConfig.pps_from_mbps(args.mbps, args.size) if args.mbps is not None else args.pps
        cfg = SenderConfig(
            group=group, port=args.port, iface_ip=ip, iface_index=idx, ttl=args.ttl,
            loop=args.loop, dscp=args.dscp, size=args.size, rate_pps=pps,
            count=args.count, duration=args.duration,
        )
        s = Sender(cfg)
        s.start()
        senders.append(s)
        rate = f"{pps:,.0f} pps / {cfg.target_mbps:,.2f} Mbps" if pps else "max rate"
        print(f"Sending to {group}:{args.port}  size={args.size}  {rate}  ttl={args.ttl}  stream={s.stream_id:08x}")

    stop = _install_stop_handler()
    meters = [RateMeter() for _ in senders]
    for m in meters:
        m.update(0, 0)
    while not stop.wait(args.interval):
        parts = []
        for s, m in zip(senders, meters):
            snap = s.snapshot()
            pps, bps = m.update(snap.packets, snap.bytes)
            parts.append(f"{s.cfg.group}: {snap.packets:,} pkts {pps:,.0f} pps {bps / 1e6:,.2f} Mbps"
                         + (f" err={snap.errors}" if snap.errors else ""))
        print("  |  ".join(parts), flush=True)
        if not any(s.running for s in senders):
            break
    for s in senders:
        s.stop()
    rc = 0
    for s in senders:
        snap = s.snapshot()
        if snap.error:
            print(f"{s.cfg.group}: ERROR {snap.error}", file=sys.stderr)
            rc = 1
        print(f"{s.cfg.group}: sent {snap.packets:,} packets ({snap.bytes:,} bytes) in {snap.elapsed:.1f}s,"
              f" {snap.errors} send errors")
    return rc


def cmd_recv(args) -> int:
    receivers: list[Receiver] = []
    for group in args.group:
        ip, idx = _iface_params(args.iface, group)
        r = Receiver(ReceiverConfig(group, args.port, ip, idx, args.source, not args.include_local))
        r.start()
        receivers.append(r)
        how = f"source {args.source} (SSM)" if args.source else "any source"
        print(f"Joined {group}:{args.port} from {how} on {args.iface or 'default interface'}")

    stop = _install_stop_handler()
    meters = [RateMeter() for _ in receivers]
    for m in meters:
        m.update(0, 0)
    started = time.monotonic()
    csv_file = open(args.csv, "w", newline="") if args.csv else None
    writer = csv.writer(csv_file) if csv_file else None
    if writer:
        writer.writerow(["time", "group", "port", "packets", "pps", "mbps", "lost", "loss_pct",
                         "duplicates", "out_of_order", "jitter_ms"])
    try:
        while not stop.wait(args.interval):
            parts = []
            for r, m in zip(receivers, meters):
                snap = r.snapshot()
                pps, bps = m.update(snap.packets, snap.bytes)
                parts.append(f"{r.cfg.group}: {snap.packets:,} pkts {pps:,.0f} pps {bps / 1e6:,.2f} Mbps "
                             f"lost={snap.lost} ({snap.loss_pct:.2f}%) dup={snap.duplicates} "
                             f"ooo={snap.out_of_order} jit={snap.jitter_ms:.2f}ms [{snap.status}]")
                if writer:
                    writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), r.cfg.group, r.cfg.port, snap.packets,
                                     f"{pps:.1f}", f"{bps / 1e6:.3f}", snap.lost, f"{snap.loss_pct:.3f}",
                                     snap.duplicates, snap.out_of_order, f"{snap.jitter_ms:.3f}"])
            print("\n".join(parts), flush=True)
            if args.duration and time.monotonic() - started >= args.duration:
                break
    finally:
        for r in receivers:
            r.stop()
        if csv_file:
            csv_file.close()

    summary = []
    for r in receivers:
        snap = r.snapshot()
        summary.append({
            "group": r.cfg.group, "port": r.cfg.port, "source": r.cfg.source,
            "packets": snap.packets, "bytes": snap.bytes, "lost": snap.lost,
            "loss_pct": round(snap.loss_pct, 4), "duplicates": snap.duplicates,
            "out_of_order": snap.out_of_order, "jitter_ms": round(snap.jitter_ms, 3),
            "join_to_first_packet_ms": snap.join_to_first_ms,
            "senders": snap.senders, "error": snap.error,
        })
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for s in summary:
            first = f"{s['join_to_first_packet_ms']:.1f} ms" if s["join_to_first_packet_ms"] is not None else "n/a"
            print(f"{s['group']}:{s['port']}: {s['packets']:,} packets, lost {s['lost']} ({s['loss_pct']}%), "
                  f"dup {s['duplicates']}, ooo {s['out_of_order']}, jitter {s['jitter_ms']} ms, "
                  f"join->first packet {first}, senders {', '.join(s['senders']) or 'none'}")
    return 1 if any(s["error"] for s in summary) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="emt-cli", description=f"{APP_NAME} (command line)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ifaces", help="list network interfaces").set_defaults(func=cmd_ifaces)

    def common(sp):
        sp.add_argument("-g", "--group", action="append", required=True,
                        help="multicast group (repeat for several groups)")
        sp.add_argument("-p", "--port", type=int, default=5000, help="UDP port (default 5000)")
        sp.add_argument("-i", "--iface", help="interface name or IP (default: OS chooses)")
        sp.add_argument("--interval", type=float, default=1.0, help="status print interval, seconds")

    s = sub.add_parser("send", help="send multicast traffic")
    common(s)
    s.add_argument("--ttl", type=int, default=16, help="multicast TTL / hop limit (default 16)")
    s.add_argument("--dscp", type=int, default=0, help="DSCP value 0-63")
    s.add_argument("--size", type=int, default=1316, help="UDP payload bytes (default 1316)")
    rate = s.add_mutually_exclusive_group()
    rate.add_argument("--pps", type=float, default=100.0, help="packets per second, 0 = max (default 100)")
    rate.add_argument("--mbps", type=float, help="target rate in Mbps (UDP payload)")
    s.add_argument("--count", type=int, default=0, help="stop after N packets")
    s.add_argument("--duration", type=float, default=0.0, help="stop after N seconds")
    s.add_argument("--loop", action="store_true",
                   help="also deliver packets to this host (off by default; for same-machine tests)")
    s.set_defaults(func=cmd_send)

    r = sub.add_parser("recv", help="join groups and measure received traffic")
    common(r)
    r.add_argument("-s", "--source", help="source IP for a source-specific (SSM/IGMPv3) join")
    r.add_argument("--include-local", action="store_true",
                   help="count packets sent from this host (ignored by default)")
    r.add_argument("--duration", type=float, default=0.0, help="leave after N seconds")
    r.add_argument("--csv", help="write periodic stats to this CSV file")
    r.add_argument("--json", action="store_true", help="print the final summary as JSON")
    r.set_defaults(func=cmd_recv)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError, NotImplementedError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
