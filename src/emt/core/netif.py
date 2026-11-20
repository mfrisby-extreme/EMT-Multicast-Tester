"""Network interface discovery (via the pure-Python `ifaddr` package)."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

import ifaddr


@dataclass(frozen=True)
class Interface:
    name: str  # OS name (en0, eth0, or a GUID on Windows)
    nice_name: str  # Friendly name (same as name except on Windows)
    index: int  # OS interface index, needed for IPv6
    ipv4: tuple[str, ...]
    ipv6: tuple[str, ...]

    @property
    def ipv4_addr(self) -> str | None:
        return self.ipv4[0] if self.ipv4 else None

    @property
    def is_loopback(self) -> bool:
        return any(ipaddress.ip_address(a).is_loopback for a in self.ipv4 + self.ipv6)

    @property
    def label(self) -> str:
        addr = self.ipv4_addr or (self.ipv6[0] if self.ipv6 else "no address")
        name = self.nice_name if self.nice_name == self.name else f"{self.nice_name} ({self.name})"
        return f"{name} - {addr}"

    def params_for(self, family: int) -> tuple[str | None, int]:
        """(interface IPv4 address, interface index) for socket setup."""
        if family == socket.AF_INET:
            return self.ipv4_addr, self.index
        return None, self.index


def _index_of(adapter) -> int:
    idx = getattr(adapter, "index", None)
    if idx:
        return int(idx)
    try:
        return socket.if_nametoindex(adapter.name)
    except (OSError, AttributeError):
        return 0


def list_interfaces() -> list[Interface]:
    """All interfaces that have at least one IP address, non-loopback first."""
    result: list[Interface] = []
    for a in ifaddr.get_adapters():
        v4 = tuple(ip.ip for ip in a.ips if isinstance(ip.ip, str))
        v6 = tuple(ip.ip[0] for ip in a.ips if isinstance(ip.ip, tuple))
        if not v4 and not v6:
            continue
        result.append(Interface(a.name, a.nice_name or a.name, _index_of(a), v4, v6))
    result.sort(key=lambda i: (i.is_loopback, not i.ipv4, i.name))
    return result


def local_addresses() -> set[str]:
    """Every IP address on this host (used to recognise our own looped-back traffic)."""
    addrs = {"127.0.0.1", "::1"}
    for itf in list_interfaces():
        addrs.update(itf.ipv4)
        addrs.update(itf.ipv6)
    return addrs


def resolve_interface(spec: str | None) -> Interface | None:
    """Find an interface by OS name, friendly name or one of its addresses.

    None / "" / "auto" means "let the OS choose". An IPv4 address that is not
    found locally is still accepted, so the OS can report the real error.
    """
    if not spec or spec.lower() == "auto":
        return None
    for itf in list_interfaces():
        if spec in (itf.name, itf.nice_name) or spec in itf.ipv4 or spec in itf.ipv6:
            return itf
    try:
        ip = ipaddress.ip_address(spec)
    except ValueError:
        raise ValueError(f"No interface named or addressed '{spec}'. Try: emt-cli ifaces") from None
    if ip.version == 4:
        return Interface(spec, spec, 0, (spec,), ())
    raise ValueError(f"No interface has address {spec}")
