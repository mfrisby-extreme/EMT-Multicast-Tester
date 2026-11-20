"""Cross-platform multicast socket setup.

This is where Windows, macOS and Linux differ, so every OS-specific quirk
lives in this one file:

* Receivers on Windows must bind to the wildcard address; on macOS/Linux we
  bind to the group address so the socket only sees that group's traffic.
* macOS needs SO_REUSEPORT for several receivers to share a port.
* Linux delivers *every* joined group on a port to every wildcard-bound
  socket unless IP_MULTICAST_ALL is turned off.
* Source-specific (SSM / IGMPv3) joins use option numbers and struct layouts
  that differ per OS and are not always exported by Python's socket module.
* IP_MULTICAST_TTL / _LOOP take a DWORD on Windows and a u_char elsewhere.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
from typing import Callable

IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    _ADD_SRC, _DROP_SRC, _V6_JOIN, _V6_LEAVE = 15, 16, 12, 13
elif IS_LINUX:
    _ADD_SRC, _DROP_SRC, _V6_JOIN, _V6_LEAVE = 39, 40, 20, 21
else:  # macOS and the BSDs
    _ADD_SRC, _DROP_SRC, _V6_JOIN, _V6_LEAVE = 70, 71, 12, 13

IP_ADD_SOURCE_MEMBERSHIP = getattr(socket, "IP_ADD_SOURCE_MEMBERSHIP", _ADD_SRC)
IP_DROP_SOURCE_MEMBERSHIP = getattr(socket, "IP_DROP_SOURCE_MEMBERSHIP", _DROP_SRC)
IPV6_JOIN_GROUP = getattr(socket, "IPV6_JOIN_GROUP", _V6_JOIN)
IPV6_LEAVE_GROUP = getattr(socket, "IPV6_LEAVE_GROUP", _V6_LEAVE)
IP_MULTICAST_ALL = getattr(socket, "IP_MULTICAST_ALL", 49)  # Linux only

SEND_BUFFER = 1 << 20
RECV_BUFFER = 4 << 20

LeaveFn = Callable[[], None]


def family_for(group: str) -> socket.AddressFamily:
    """Validate a multicast group address and return its address family."""
    try:
        ip = ipaddress.ip_address(group)
    except ValueError:
        raise ValueError(f"'{group}' is not a valid IP address") from None
    if not ip.is_multicast:
        raise ValueError(f"{group} is not a multicast address")
    return socket.AF_INET if ip.version == 4 else socket.AF_INET6


def group_range(first: str, count: int) -> list[str]:
    """`count` consecutive multicast groups starting at `first` (234.5.6.7 x3 -> .7, .8, .9)."""
    family_for(first)
    if count < 1:
        raise ValueError("group count must be at least 1")
    base = ipaddress.ip_address(first)
    groups = []
    for i in range(count):
        try:
            ip = base + i
        except (ipaddress.AddressValueError, ValueError):
            ip = None
        if ip is None or not ip.is_multicast:
            raise ValueError(f"{count} groups starting at {first} would run past the end of the multicast range")
        groups.append(str(ip))
    return groups


def is_ssm_range(group: str) -> bool:
    """True for the IANA SSM ranges (232.0.0.0/8, ff3x::/32)."""
    ip = ipaddress.ip_address(group)
    if ip.version == 4:
        return ip in ipaddress.ip_network("232.0.0.0/8")
    return ip.packed[0] == 0xFF and (ip.packed[1] >> 4) == 0x3


def _u8(value: int) -> int | bytes:
    return value if IS_WIN else struct.pack("B", value)


def _try_setsockopt(sock: socket.socket, level: int, opt: int, value: int | bytes) -> bool:
    try:
        sock.setsockopt(level, opt, value)
        return True
    except OSError:
        return False


def apply_sender_options(sock: socket.socket, ttl: int, loop: bool, dscp: int) -> None:
    """(Re)apply TTL / hop limit, loopback and DSCP on a sender socket. Safe on a live socket."""
    if sock.family == socket.AF_INET:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, _u8(ttl))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, _u8(int(loop)))
        # Note: Windows ignores IP_TOS unless a QoS policy allows it.
        _try_setsockopt(sock, socket.IPPROTO_IP, socket.IP_TOS, dscp << 2)
    else:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, ttl)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_LOOP, int(loop))
        tclass = getattr(socket, "IPV6_TCLASS", None)
        if tclass is not None:
            _try_setsockopt(sock, socket.IPPROTO_IPV6, tclass, dscp << 2)


def open_sender(
    group: str,
    port: int,
    iface_ip: str | None = None,
    iface_index: int = 0,
    ttl: int = 16,
    loop: bool = True,
    dscp: int = 0,
) -> tuple[socket.socket, tuple]:
    """Create a UDP socket ready to send to `group`. Returns (socket, destination address)."""
    fam = family_for(group)
    sock = socket.socket(fam, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        _try_setsockopt(sock, socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER)
        apply_sender_options(sock, ttl, loop, dscp)
        if fam == socket.AF_INET:
            if iface_ip:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
                # Binding pins the source address on multi-homed hosts.
                sock.bind((iface_ip, 0))
            dest: tuple = (group, port)
        else:
            if iface_index:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, iface_index)
            dest = (group, port, 0, iface_index)
    except Exception:
        sock.close()
        raise
    return sock, dest


def _bind_receiver(sock: socket.socket, fam: int, group: str, port: int, scope_id: int) -> None:
    wildcard = ("", port) if fam == socket.AF_INET else ("::", port, 0, 0)
    if IS_WIN:
        sock.bind(wildcard)
        return
    try:
        sock.bind((group, port) if fam == socket.AF_INET else (group, port, 0, scope_id))
    except OSError:
        sock.bind(wildcard)


def open_receiver(
    group: str,
    port: int,
    iface_ip: str | None = None,
    iface_index: int = 0,
    source: str | None = None,
) -> tuple[socket.socket, LeaveFn]:
    """Create a socket, bind it and join `group`.

    Joining is what makes the OS send the IGMP/MLD report. Returns
    (socket, leave) where `leave()` drops the membership (sending the
    IGMP leave) - call it before closing the socket.
    """
    fam = family_for(group)
    sock = socket.socket(fam, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if not IS_WIN and hasattr(socket, "SO_REUSEPORT"):
            _try_setsockopt(sock, socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        _try_setsockopt(sock, socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER)

        if fam == socket.AF_INET:
            if IS_LINUX:
                _try_setsockopt(sock, socket.IPPROTO_IP, IP_MULTICAST_ALL, 0)
            _bind_receiver(sock, fam, group, port, 0)

            grp = socket.inet_aton(group)
            ifc = socket.inet_aton(iface_ip or "0.0.0.0")
            if source:
                src = socket.inet_aton(source)
                # struct ip_mreq_source field order differs on Linux.
                mreq = grp + ifc + src if IS_LINUX else grp + src + ifc
                add, drop = IP_ADD_SOURCE_MEMBERSHIP, IP_DROP_SOURCE_MEMBERSHIP
            else:
                mreq = grp + ifc
                add, drop = socket.IP_ADD_MEMBERSHIP, socket.IP_DROP_MEMBERSHIP
            sock.setsockopt(socket.IPPROTO_IP, add, mreq)

            def leave() -> None:
                _try_setsockopt(sock, socket.IPPROTO_IP, drop, mreq)

        else:
            if source:
                raise NotImplementedError("Source-specific (SSM) joins are not supported for IPv6 yet")
            _bind_receiver(sock, fam, group, port, iface_index)
            mreq6 = socket.inet_pton(socket.AF_INET6, group) + struct.pack("@I", iface_index)
            sock.setsockopt(socket.IPPROTO_IPV6, IPV6_JOIN_GROUP, mreq6)

            def leave() -> None:
                _try_setsockopt(sock, socket.IPPROTO_IPV6, IPV6_LEAVE_GROUP, mreq6)

    except Exception:
        sock.close()
        raise
    return sock, leave
