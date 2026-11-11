"""core/platforms - every OS-dependent behaviour of the node, in one place.

The node serves the same fifteen MCP tools on Linux, Windows and an Android
phone, but almost nothing behind them is portable: the command shell, the
telemetry source, the way a pid is inspected, and whether a screenshot can be
taken at all differ per system.  Before this package those differences lived as
``if os.name == "nt"`` branches scattered through ``core/mcp_tools.py``,
``core/vitals.py``, ``core/device.py`` and ``core/termux.py``; a change on one
platform could silently break another, and no single file could answer "what
does this node do on a phone?".

Layout::

    core/platforms/
        __init__.py      detection + the backend registry (``backend()``)
        base.py          the contract every platform implements
        common.py        helper code that is genuinely portable
        shell.py         which shell runs a command (pure decision)
        encoding.py      how captured child output is decoded (pure decision)
        windows/         Win32 through ctypes: no PowerShell, no WMI, no git
        linux/           /proc and /sys: no df, no free, no ps, no uptime
        android/         /proc and /sys first, Termux:API only where Android
                         gives no other way (camera, SMS, location)

Two rules hold for every backend:

* **standard library only** - no third-party package is imported, and no
  external program is required for a tool to answer.  A stock Windows install
  has PowerShell, but a hardened one may not; a phone has no ``df``; a
  container may have no ``ps``.  Anything the OS can be asked directly is asked
  directly.
* **nothing raises** - a backend method returns a value or a block that says
  ``available: false`` with a reason.  A tool call failing because a counter was
  unreadable is worse than the counter being absent.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any, Dict, Optional

from core.platforms.base import PlatformBackend

#: Every scenario this package implements, in the order detection tries them.
SCENARIOS = ("windows", "termux", "darwin", "linux")

_LOCK = threading.RLock()
_BACKENDS: Dict[str, PlatformBackend] = {}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _termux_data_dir() -> bool:
    """True when Termux's fixed application data directory exists.

    Termux always has ``/data/data/com.termux/files/usr``; the check survives a
    scrubbed environment (a ``su`` shell, a service started by Termux:Boot),
    which is why it is one of the three signals.
    """
    try:
        return os.path.isdir("/data/data/com.termux/files/usr")
    except Exception:
        return False


def detect(termux_data_dir: Optional[bool] = None,
           env: Optional[Dict[str, str]] = None,
           system: Optional[str] = None) -> str:
    """The scenario this process runs in: ``windows``, ``termux``, ``darwin`` or ``linux``.

    The order is the contract, not a preference:

    1. ``os.name == "nt"`` wins outright - a Windows box that happens to export
       ``TERMUX_VERSION`` (a CI matrix, a stray WSL variable) is still Windows;
    2. Termux comes next, because a phone reports ``Linux`` from
       ``platform.system()`` and would otherwise be mistaken for a server;
    3. Darwin, because macOS is POSIX too but owns its own tools;
    4. Linux is the fallback: a server, a container and any other POSIX host.

    ``termux_data_dir``, ``env`` and ``system`` are injectable so a caller with
    better information (``core/device.py`` already probes the Termux data
    directory for its own report, and a test patches it) supplies the evidence
    instead of this function looking for it a second, divergent way.
    """
    if os.name == "nt":
        return "windows"
    environment = os.environ if env is None else env
    try:
        if environment.get("TERMUX_VERSION") or "com.termux" in environment.get("PREFIX", ""):
            return "termux"
    except Exception:
        pass
    if termux_data_dir is None:
        termux_data_dir = _termux_data_dir()
    if termux_data_dir:
        return "termux"
    platform_name = system
    if platform_name is None:
        import platform as _platform

        platform_name = _platform.system()
    if platform_name in ("Darwin", "darwin"):
        return "darwin"
    return "linux"


def detect_report() -> Dict[str, Any]:
    """Detection plus the evidence behind it, for tools and bug reports."""
    return {
        "scenario": detect(),
        "os_name": os.name,
        "platform": sys.platform,
        "signals": {
            "TERMUX_VERSION": os.environ.get("TERMUX_VERSION", ""),
            "PREFIX": os.environ.get("PREFIX", ""),
            "termux_data_dir": _termux_data_dir(),
        },
        "checked": list(SCENARIOS),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _build(scenario: str) -> PlatformBackend:
    """Construct the backend for *scenario* (imported lazily: a Linux host must
    not import Win32 ctypes declarations, and a Windows host must not read
    /proc)."""
    if scenario == "windows":
        from core.platforms.windows import WindowsBackend

        return WindowsBackend()
    if scenario == "termux":
        from core.platforms.android import AndroidBackend

        return AndroidBackend()
    if scenario == "darwin":
        from core.platforms.darwin import DarwinBackend

        return DarwinBackend()
    from core.platforms.linux import LinuxBackend

    return LinuxBackend()


def backend(scenario: Optional[str] = None) -> PlatformBackend:
    """The backend for *scenario*, or for this process when it is omitted.

    Cached per scenario: every backend is stateless apart from read-only probes,
    and a telemetry call that rebuilt one per invocation would pay the ctypes
    declaration cost on every ``system_info``.
    """
    name = scenario or detect()
    with _LOCK:
        found = _BACKENDS.get(name)
        if found is None:
            found = _build(name)
            _BACKENDS[name] = found
        return found


def reset() -> None:
    """Forget every cached backend (tests switch scenarios inside one process)."""
    with _LOCK:
        _BACKENDS.clear()


__all__ = [
    "SCENARIOS",
    "PlatformBackend",
    "backend",
    "detect",
    "detect_report",
    "reset",
]
