"""core/platforms/base.py - the contract every platform backend implements.

One instance per scenario describes one host, and every tool that needs the OS
asks that instance instead of branching on ``os.name``.  The base class is
concrete rather than abstract on purpose: a capability a platform does not have
(no framebuffer on a headless server, no clipboard on a phone) is answered with
a reason from the default implementation, so a caller never has to know which
platform it is running on.

Contract rules:

* a method returns a value or an "unavailable" answer, and never raises;
* text is UTF-8 by the time it leaves this layer;
* nothing here imports ``core.mcp_tools``, ``core.device`` or ``core.termux`` -
  the dependency arrow points one way (tools -> platforms).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def unavailable(reason: str, **fields: Any) -> Dict[str, Any]:
    """A block that says why a platform cannot answer, plus whatever was measured."""
    block: Dict[str, Any] = {"available": False, "reason": reason}
    block.update(fields)
    return block


class PlatformBackend(object):
    """What the node needs to know about the OS it runs on."""

    #: ``windows`` | ``linux`` | ``termux`` | ``darwin``
    scenario = "unknown"

    #: Human name for reports (``Windows 11``, ``Android/Termux``...).
    label = "unknown"

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        """``(name, argv prefix)`` of the shell that runs ``bash_exec``/``run_job``.

        The name is what ``system_info`` reports as ``command_shell``, so the
        model writes commands that the host can actually run.
        """
        raise NotImplementedError

    def popen_flags(self) -> int:
        """``subprocess`` creation flags (0 where the concept does not exist)."""
        return 0

    def child_env(self) -> Dict[str, str]:
        """Environment for a child process.

        Windows needs ``PYTHONIOENCODING``/``PYTHONUTF8`` here: a Python child
        writing to a pipe uses the ANSI code page, which is neither UTF-8 nor the
        OEM code page the surrounding console uses, and no in-band marker says
        which of the three arrived.
        """
        return dict(os.environ)

    def output_encodings(self) -> List[str]:
        """Candidate encodings for captured child output, most likely first."""
        return ["utf-8"]

    # -- host telemetry ----------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        """Seconds since boot, or ``None`` when the OS cannot say."""
        return None

    def vitals(self) -> Dict[str, Any]:
        """CPU / RAM / disk in the shape ``system_vitals`` returns.

        Shape::

            {"hostname": str, "os": str,
             "cpu_load": {"1m": float, "5m": float, "15m": float},
             "ram": {"total_mb", "used_mb", "free_mb", "used_pct"},
             "disk": {"total_gb", "used_gb", "free_gb", "used_pct"}}
        """
        raise NotImplementedError

    def info_blocks(self) -> Dict[str, str]:
        """The four telemetry text blocks ``system_info`` embeds.

        Keys are stable across platforms - ``disks``, ``memory``, ``load``,
        ``top_processes`` - so a model that learned the shape on Linux reads the
        Windows answer the same way.  Values are already-rendered text because
        that is what the tool returns to the client.
        """
        return {}

    def wallpaper(self) -> str:
        """Current desktop wallpaper path, or ``""`` when unknown/headless."""
        return ""

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Any = None) -> bool:
        """True when *pid* is a live process.

        *start_time* guards against PID reuse where the OS exposes it.
        """
        raise NotImplementedError

    def pid_start_time(self, pid: int) -> Optional[int]:
        """An opaque, monotonic per-process start marker, or ``None``."""
        return None

    def kill_tree(self, pid: int) -> None:
        """Terminate *pid* and its descendants, best effort, never raising."""
        raise NotImplementedError

    # -- desktop capabilities ---------------------------------------------

    def screenshot_png(self) -> Optional[bytes]:
        """A PNG of the primary (or virtual) screen, or ``None`` when headless."""
        return None

    def clipboard_get(self) -> Optional[str]:
        """Clipboard text, or ``None`` when the platform has no clipboard."""
        return None

    def clipboard_set(self, text: str) -> bool:
        """Put *text* on the clipboard. False when unsupported."""
        return False

    # -- introspection -----------------------------------------------------

    def capabilities(self) -> Dict[str, bool]:
        """What this backend can actually do, for ``system_info`` and tests."""
        return {
            "shell": True,
            "vitals": True,
            "info_blocks": bool(self.info_blocks()),
            "process_tree": True,
            "screenshot": self.screenshot_png.__func__ is not PlatformBackend.screenshot_png,
            "clipboard": self.clipboard_get.__func__ is not PlatformBackend.clipboard_get,
        }

    def report(self) -> Dict[str, Any]:
        """A small self-description, safe to embed in a tool answer."""
        name, argv = self.shell()
        return {
            "scenario": self.scenario,
            "label": self.label,
            "shell": name,
            "shell_argv": argv,
            "capabilities": self.capabilities(),
        }
