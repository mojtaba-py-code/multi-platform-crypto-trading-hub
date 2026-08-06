"""The bundled dashboard must actually run under the app's security headers.

The global policy is ``default-src 'self'``, which blocks inline ``<script>``
and ``<style>`` — and the dashboard is a single self-contained file made of
exactly those. The route therefore serves a per-response nonce and a stricter,
nonce-scoped policy. These tests pin that contract, because a regression here
is invisible server-side: the page still returns 200 and simply does nothing in
the browser.
"""

from __future__ import annotations

import re

import pytest

_NONCE_RE = re.compile(r"nonce-([A-Za-z0-9_-]+)")


@pytest.mark.asyncio
async def test_dashboard_csp_allows_its_own_inline_blocks(client):
    r = await client.get("/dashboard")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]

    nonces = set(_NONCE_RE.findall(csp))
    assert len(nonces) == 1, "script-src and style-src must share one nonce"
    nonce = nonces.pop()

    # Both inline blocks carry the nonce, so the browser will execute them.
    assert f'<style nonce="{nonce}">' in r.text
    assert f'<script nonce="{nonce}">' in r.text
    # The placeholder must be fully substituted.
    assert "{{CSP_NONCE}}" not in r.text


@pytest.mark.asyncio
async def test_dashboard_policy_is_strict(client):
    csp = (await client.get("/dashboard")).headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    # 'unsafe-inline'/'unsafe-eval' would defeat the point of the nonce.
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


@pytest.mark.asyncio
async def test_dashboard_nonce_is_per_response(client):
    first = (await client.get("/dashboard")).headers["content-security-policy"]
    second = (await client.get("/dashboard")).headers["content-security-policy"]
    assert _NONCE_RE.findall(first) != _NONCE_RE.findall(second)


@pytest.mark.asyncio
async def test_dashboard_has_no_inline_event_handlers(client):
    """Nonces cannot whitelist ``onclick=``-style attributes, so there are none."""
    body = (await client.get("/dashboard")).text
    assert not re.search(r"\son[a-z]+\s*=\s*[\"']", body), (
        "inline event-handler attributes are blocked by the dashboard's CSP; "
        "bind handlers with addEventListener instead"
    )


@pytest.mark.asyncio
async def test_dashboard_has_no_inline_style_attributes(client):
    """A nonce covers ``<style>`` blocks but never ``style="..."`` attributes.

    Those are silently dropped by the browser — the page still returns 200 and
    just renders wrong — so they are caught here instead.
    """
    body = (await client.get("/dashboard")).text
    offenders = re.findall(r"<[^>]*\sstyle\s*=\s*\"[^\"]*\"[^>]*>", body)
    assert not offenders, (
        "inline style attributes are blocked by the dashboard's CSP; "
        f"move them into the nonced <style> block: {offenders}"
    )


@pytest.mark.asyncio
async def test_api_responses_keep_the_default_strict_headers(client):
    r = await client.get("/api/v1/health")
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
