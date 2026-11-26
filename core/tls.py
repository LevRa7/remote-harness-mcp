"""core/tls.py - one place that decides which CA bundle the node trusts.

A Windows Python verifies HTTPS against the system ROOT store. On a machine
whose store predates the current public roots (a restored image, a long-lived
VM, an offline installer) every request fails with::

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

although the network is fine. Node and browsers keep working because they ship
their own bundle; Python does not, so a zero-dependency node needs an explicit
decision instead of whatever the platform happens to contain.

Order, most specific first:

1. ``MESH_CA_BUNDLE`` / ``SSL_CERT_FILE`` - an operator (or a TLS-inspecting
   proxy) points at the PEM to trust; this always wins.
2. ``certifi`` when it is importable - the Mozilla bundle already on disk on
   most machines (any project using requests/httpx has it).
3. the platform store - the historic behaviour, kept as the last resort.

``MESH_TLS_INSECURE`` is deliberately *not* handled here: only ``fetch_url``
honours it, and only as an explicit last resort, because accepting any
certificate would make the SSRF guard meaningless.
"""

from __future__ import annotations

import os
import ssl
from typing import List, Optional, Tuple

#: Explicit CA bundle (PEM). A path that does not exist is ignored, not fatal.
CA_BUNDLE_ENV = "MESH_CA_BUNDLE"

#: Environment variables consulted for a bundle, in order.
_BUNDLE_ENVS = (CA_BUNDLE_ENV, "SSL_CERT_FILE")


def ca_bundle_path() -> Optional[str]:
    """Path of the PEM to trust, or None when the platform store should be used."""
    for name in _BUNDLE_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value and os.path.isfile(value):
            return value
    try:
        import certifi  # optional: the Mozilla bundle, usually already present
        path = certifi.where()
        if os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


def context_for(path: str) -> Optional[ssl.SSLContext]:
    """A verifying context bound to *path*, or None when the file is unusable."""
    try:
        return ssl.create_default_context(cafile=path)
    except Exception:
        return None


def context() -> ssl.SSLContext:
    """The best verifying context available (bundle first, platform store after)."""
    path = ca_bundle_path()
    if path:
        ctx = context_for(path)
        if ctx is not None:
            return ctx
    return ssl.create_default_context()


def stores() -> List[Tuple[str, ssl.SSLContext]]:
    """Every verifying store to try, labelled, without duplicates."""
    found: List[Tuple[str, ssl.SSLContext]] = []

    def add(label: str, ctx: Optional[ssl.SSLContext]) -> None:
        if ctx is None or any(ctx is existing for _, existing in found):
            return
        found.append((label, ctx))

    for name in _BUNDLE_ENVS:
        value = (os.environ.get(name) or "").strip()
        if value and os.path.isfile(value):
            add(name, context_for(value))

    bundle = ca_bundle_path()
    if bundle and not any(label in _BUNDLE_ENVS for label, _ in found):
        add("certifi", context_for(bundle))

    try:
        add("system", ssl.create_default_context())
    except Exception:
        pass
    return found
