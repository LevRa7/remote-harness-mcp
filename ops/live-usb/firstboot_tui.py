#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops/live-usb/firstboot_tui.py - Zero-dependency ANSI console dashboard for tty1.

Displays network connectivity, USB tethering / Wi-Fi hints, disk health summary,
and renders an ASCII/UTF-8 QR code or pairing URL for the Remote-Harness Web UI.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess

from core import domain


def _get_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "OFFLINE (Connect Ethernet, Android USB Tethering, or press [W] for Wi-Fi)"


def _render_qr(url: str) -> str:
    if shutil.which("qrencode"):
        try:
            proc = subprocess.run(
                ["qrencode", "-t", "ANSIUTF8", url],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
    return f"  [QR Code unavailable - open URL manually]:\n  {url}\n"


def render_banner() -> str:
    ip = _get_ipv4()
    hostname = socket.gethostname()
    pub_domain = getattr(domain, "PUBLIC_DOMAIN", "mesh-gateway.local")
    pair_url = os.environ.get("MESH_PAIR_URL", f"https://{pub_domain}/rescue/{hostname}")
    lines = [
        "\033[2J\033[H",
        "======================================================================",
        "   REMOTE-HARNESS MCP :: LIVE USB RESCUE & DIAGNOSTICS AGENT",
        "======================================================================",
        f"  Node Hostname : {hostname}",
        f"  Scenario      : live_usb (Read-Only Safe-Mount Mode Enabled)",
        f"  Network IPv4  : {ip}",
        f"  Rescue Web UI : {pair_url}",
        "----------------------------------------------------------------------",
        _render_qr(pair_url),
        "----------------------------------------------------------------------",
        "  [W] Configure Wi-Fi (nmtui)   [S] Shell   [R] Restart Tunnel Agent",
        "======================================================================",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_banner())
