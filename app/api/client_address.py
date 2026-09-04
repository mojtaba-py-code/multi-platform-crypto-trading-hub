"""Determining the address a request actually came from.

Behind a reverse proxy, ``request.client.host`` is the *proxy's* address, not
the caller's. Every request then looks like it came from one machine, which
quietly breaks two things at once:

* the per-IP rate limiter collapses into a single shared bucket, so the whole
  deployment shares one quota and one noisy client can lock out everybody;
* the audit trail records the proxy for every login and lockout, which is
  exactly the field an incident responder needs.

``X-Forwarded-For`` carries the real chain, but it is a plain request header —
anyone talking to the application directly can invent one. Honouring it
unconditionally would let an attacker forge a different address per request and
walk straight through both controls.

So the header is trusted only when the machine that actually opened the
connection is a configured proxy (``TRUSTED_PROXY_IPS``). With nothing
configured — the default — the header is ignored entirely and the peer address
is used, which is the correct behaviour for an application exposed directly.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request

from app.config import get_settings

_Network = IPv4Network | IPv6Network


@functools.lru_cache(maxsize=8)
def _parse_networks(raw: tuple[str, ...]) -> tuple[_Network, ...]:
    """Parse configured proxies into networks, ignoring unparseable entries.

    Keyed on the raw tuple rather than read from settings directly, so tests
    can pass their own list without having to clear a cache.
    """
    networks: list[_Network] = []
    for entry in raw:
        entry = entry.strip()
        if not entry:
            continue
        try:
            # A bare address parses as a single-host network (/32 or /128).
            networks.append(ip_network(entry, strict=False))
        except ValueError:
            # A typo must not silently widen trust, so the entry is dropped.
            continue
    return tuple(networks)


def _is_trusted(candidate: str, networks: Sequence[_Network]) -> bool:
    try:
        address = ip_address(candidate)
    except ValueError:
        # Not an address at all (an obfuscated or malformed hop) — never trusted.
        return False
    return any(address in network for network in networks)


def resolve_client_ip(request: Request, *, trusted: Sequence[str] | None = None) -> str | None:
    """Return the caller's address, honouring ``X-Forwarded-For`` only when safe.

    The chain is walked from the right, which is the end the proxy appends to
    and therefore the only part an upstream cannot forge: each trusted hop is
    skipped and the first address that is not a known proxy is the client. A
    forged prefix supplied by the caller sits further left and is never reached.
    """
    peer = request.client.host if request.client else None
    if trusted is None:
        trusted = get_settings().trusted_proxy_ips
    networks = _parse_networks(tuple(trusted))

    if peer is None or not networks or not _is_trusted(peer, networks):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed([hop.strip() for hop in forwarded.split(",") if hop.strip()]):
        if not _is_trusted(candidate, networks):
            return candidate
    # Every hop was a trusted proxy (or the header was absent): the nearest
    # real address we have is the peer itself.
    return peer
