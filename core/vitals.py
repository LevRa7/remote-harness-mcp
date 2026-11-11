"""core/vitals.py - host vitals, delegated to the platform backends.

This module used to hold four platform branches of its own (``GetSystemTimes``
for Windows, ``/proc/meminfo`` for Linux, ``sysctl`` for macOS, ``shutil`` for the
disk).  That logic now lives with the rest of each platform's Win32/procfs code:

* Windows - :mod:`core.platforms.windows.telemetry` (kernel calls, no PowerShell);
* Linux   - :mod:`core.platforms.linux.telemetry` (``/proc``, no ``free``/``df``);
* Android - :mod:`core.platforms.android.telemetry` (``/proc`` + ``/sys``, so the
  battery block does not need the Termux:API app);
* macOS   - :class:`core.platforms.linux.DarwinBackend`.

The public function keeps its name and its shape because ``core/mcp_tools.py``,
``core/server.py`` and ``core/device.py`` all import it, and because a caller
should not have to know which backend answered.
"""

from __future__ import annotations

import platform
from typing import Any, Dict, Optional

from core import platforms

#: The shape ``get_host_vitals`` guarantees even when every probe failed.
_EMPTY: Dict[str, Any] = {
    "hostname": "",
    "os": "",
    "cpu_load": {"1m": 0.0, "5m": 0.0, "15m": 0.0},
    "ram": {"total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0, "used_pct": 0.0},
    "disk": {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "used_pct": 0.0},
}


def _windows_cpu_usage_pct(sample_seconds: float = 0.1) -> float:
    """Measured CPU utilisation on Windows (kept for callers that import it).

    Windows has no load average; ``GetSystemTimes`` difference over a short
    sample is the utilisation every other Windows tool reports.
    """
    from core.platforms.windows import telemetry

    return float(telemetry.cpu_percent(sample_seconds) or 0.0)


def get_host_vitals() -> Dict[str, Any]:
    """CPU load, RAM and disk of this host, in the shape every consumer expects.

    Nothing here raises: a backend that cannot answer leaves a zeroed block, and
    a caller can render that instead of failing a tool call.

    On a phone ``platform.system()`` is ``"Android"`` (Python 3.13+) even though
    ``/proc`` is right there; the backend registry decides by evidence, not by
    that string, which is why this function no longer has its own detection.
    """
    try:
        vitals = platforms.backend().vitals()
    except Exception:
        vitals = None
    if not isinstance(vitals, dict):
        vitals = {}
    result = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in _EMPTY.items()}
    for key, value in vitals.items():
        if value is not None:
            result[key] = value
    # Fill anything a backend left blank so the shape never varies.
    for section in ("cpu_load", "ram", "disk"):
        if not isinstance(result.get(section), dict):
            result[section] = dict(_EMPTY[section])
    if not result.get("hostname"):
        import socket

        try:
            result["hostname"] = socket.gethostname()
        except Exception:
            result["hostname"] = "unknown"
    if not result.get("os"):
        result["os"] = platform.system()
    return result


__all__ = ["get_host_vitals"]
