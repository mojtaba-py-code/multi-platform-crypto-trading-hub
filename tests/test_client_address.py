"""Resolving the caller's address behind a reverse proxy.

Two failure modes sit on either side of this code, and both are silent:

* Ignoring ``X-Forwarded-For`` behind a proxy makes every request look like it
  came from the proxy. The per-IP rate limiter becomes one shared bucket for
  the whole deployment, and every audit entry records the proxy.
* Honouring the header without checking who sent it lets any caller invent an
  address per request, which walks straight through the rate limiter and
  poisons the audit trail with addresses of the attacker's choosing.

The rule under test is that the header counts only when the machine that opened
the connection is a configured proxy.
"""

from __future__ import annotations

import pytest
from app.api.client_address import resolve_client_ip
from starlette.requests import Request

_PROXY = "10.0.0.1"
_CLIENT = "203.0.113.7"


def _request(peer: str | None, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 51234) if peer is not None else None,
        }
    )


# --- Default: trust nothing -------------------------------------------------


def test_without_configured_proxies_the_header_is_ignored():
    """The default has to be safe for an app exposed directly to the internet."""
    request = _request(_CLIENT, forwarded="1.2.3.4")
    assert resolve_client_ip(request, trusted=[]) == _CLIENT


def test_an_untrusted_caller_cannot_forge_its_own_address():
    """The whole point: a spoofed header from a stranger changes nothing."""
    request = _request("198.51.100.9", forwarded=f"{_CLIENT}, 1.2.3.4")
    assert resolve_client_ip(request, trusted=[_PROXY]) == "198.51.100.9"


# --- Behind a trusted proxy -------------------------------------------------


def test_a_trusted_proxy_reveals_the_real_client():
    request = _request(_PROXY, forwarded=_CLIENT)
    assert resolve_client_ip(request, trusted=[_PROXY]) == _CLIENT


def test_the_chain_is_read_from_the_right():
    """Only the rightmost entries are written by infrastructure we control.

    A caller can put anything in the header before it arrives; the proxy then
    *appends* the address it actually saw. Reading from the left would return
    the attacker's invention.
    """
    request = _request(_PROXY, forwarded=f"attacker-supplied, 1.2.3.4, {_CLIENT}")
    assert resolve_client_ip(request, trusted=[_PROXY]) == _CLIENT


def test_multiple_trusted_hops_are_skipped():
    """Two proxies in front means two appended entries to walk past."""
    request = _request("10.0.0.2", forwarded=f"{_CLIENT}, {_PROXY}")
    assert resolve_client_ip(request, trusted=["10.0.0.1", "10.0.0.2"]) == _CLIENT


def test_a_cidr_range_matches():
    """Container addresses are assigned dynamically, so ranges must work."""
    request = _request("172.18.0.5", forwarded=_CLIENT)
    assert resolve_client_ip(request, trusted=["172.16.0.0/12"]) == _CLIENT


def test_a_trusted_proxy_with_no_header_falls_back_to_the_peer():
    request = _request(_PROXY)
    assert resolve_client_ip(request, trusted=[_PROXY]) == _PROXY


def test_a_chain_of_only_proxies_falls_back_to_the_peer():
    """Nothing in the chain is a real client, so do not invent one."""
    request = _request(_PROXY, forwarded="10.0.0.2, 10.0.0.3")
    assert resolve_client_ip(request, trusted=["10.0.0.0/24"]) == _PROXY


# --- Malformed input --------------------------------------------------------


@pytest.mark.parametrize("junk", ["not-an-ip", "", "999.999.999.999", "<script>"])
def test_unparseable_hops_are_never_treated_as_trusted(junk):
    """A hop that is not an address cannot match a network, so it is a client."""
    request = _request(_PROXY, forwarded=f"{_CLIENT}, {junk}")
    resolved = resolve_client_ip(request, trusted=[_PROXY])
    # Either the junk itself (it is the rightmost non-proxy) or the real client
    # — never a crash, and never silently trusted as a proxy.
    assert resolved in {junk, _CLIENT} and resolved != _PROXY


def test_a_malformed_proxy_entry_does_not_widen_trust():
    """A typo in configuration must not accidentally trust everybody."""
    request = _request(_CLIENT, forwarded="1.2.3.4")
    assert resolve_client_ip(request, trusted=["not-a-network"]) == _CLIENT


def test_a_request_without_a_peer_resolves_to_none():
    """ASGI allows a missing client (e.g. a test transport)."""
    assert resolve_client_ip(_request(None, forwarded=_CLIENT), trusted=[_PROXY]) is None


def test_ipv6_is_handled():
    request = _request("::1", forwarded="2001:db8::1")
    assert resolve_client_ip(request, trusted=["::1"]) == "2001:db8::1"


def test_settings_supply_the_default_trust_list(monkeypatch):
    """Callers normally omit ``trusted``; it must come from configuration."""
    from app.config.settings import Settings

    monkeypatch.setattr(
        "app.api.client_address.get_settings",
        lambda: Settings(_env_file=None, trusted_proxy_ips=[_PROXY]),
    )
    assert resolve_client_ip(_request(_PROXY, forwarded=_CLIENT)) == _CLIENT
