"""fetch_url / core.tls trust-store tests (v0.7.1 regression).

A Windows Python verifies against the system ROOT store; on an image whose
store predates the current public roots every fetch fails with "unable to get
local issuer certificate". The node must then fall back to another bundled
store instead of failing the whole call - and it must never silently accept an
unverifiable certificate unless the operator opted in.
"""

from __future__ import annotations

import ssl
import urllib.error

import pytest

from core import mcp_tools
from core import tls

certifi = pytest.importorskip("certifi", reason="no bundled CA store to compare against")


@pytest.fixture(autouse=True)
def _reset_tls_cache():
    """Every test starts with no remembered store."""
    mcp_tools._ssl_store_cache = None
    yield
    mcp_tools._ssl_store_cache = None


def test_operator_bundle_wins(monkeypatch):
    monkeypatch.setenv("MESH_CA_BUNDLE", certifi.where())
    monkeypatch.delenv("MESH_TLS_INSECURE", raising=False)
    labels = [label for label, _ in mcp_tools._fetch_url_ssl_stores()]
    assert labels[0] == tls.CA_BUNDLE_ENV


def test_missing_bundle_is_ignored(monkeypatch):
    monkeypatch.setenv("MESH_CA_BUNDLE", "no/such/bundle.pem")
    labels = [label for label, _ in mcp_tools._fetch_url_ssl_stores()]
    assert tls.CA_BUNDLE_ENV not in labels
    assert "system" in labels or "certifi" in labels


def test_insecure_store_is_opt_in(monkeypatch):
    monkeypatch.delenv("MESH_CA_BUNDLE", raising=False)
    monkeypatch.delenv("MESH_TLS_INSECURE", raising=False)
    assert "insecure" not in [label for label, _ in mcp_tools._fetch_url_ssl_stores()]
    monkeypatch.setenv("MESH_TLS_INSECURE", "1")
    assert "insecure" in [label for label, _ in mcp_tools._fetch_url_ssl_stores()]


def test_context_verifies_the_bundled_roots():
    """The shared context is not an empty stand-in: it really loads CAs."""
    assert len(tls.context().get_ca_certs()) > 0


def test_verification_failure_falls_through_to_the_next_store(monkeypatch):
    """A broken ROOT store must not end the call while another store verifies."""
    calls = []

    def fake_attempt(url, context, max_bytes, timeout_sec):
        calls.append(context)
        if len(calls) == 1:
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
            )
        return url, 200, "text/plain", b"ok", False, 0

    monkeypatch.setattr(mcp_tools, "_fetch_url_attempt", fake_attempt)
    monkeypatch.setattr(
        mcp_tools,
        "_fetch_url_ssl_stores",
        lambda: [("broken", ssl.create_default_context()), ("good", ssl.create_default_context())],
    )
    monkeypatch.setattr(mcp_tools, "_validate_fetch_url", lambda url: url)

    result = mcp_tools._tool_fetch_url({"url": "https://example.com"})

    assert result["status"] == 200
    assert result["content"] == "ok"
    assert len(calls) == 2
    assert mcp_tools._ssl_store_cache is not None
    assert mcp_tools._ssl_store_cache[0] == "good"


def test_all_stores_failing_reports_the_knob(monkeypatch):
    def always_fail(url, context, max_bytes, timeout_sec):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(mcp_tools, "_fetch_url_attempt", always_fail)
    monkeypatch.setattr(mcp_tools, "_fetch_url_ssl_stores", lambda: [("a", ssl.create_default_context())])
    monkeypatch.setattr(mcp_tools, "_validate_fetch_url", lambda url: url)

    result = mcp_tools._tool_fetch_url({"url": "https://example.com"})

    assert "error" in result
    assert tls.CA_BUNDLE_ENV in result["error"]
    assert "tls_error" in result


def test_ssrf_guard_runs_before_any_tls_store(monkeypatch):
    """A blocked address is refused before a single trust store is touched."""
    touched = []
    monkeypatch.setattr(mcp_tools, "_fetch_url_ssl_stores", lambda: touched.append(1) or [])

    result = mcp_tools._tool_fetch_url({"url": "https://127.0.0.1/"})

    assert "error" in result
    assert "SSRF" in result["error"]
    assert touched == []
