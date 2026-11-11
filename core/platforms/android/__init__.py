"""core/platforms/android - the Android/Termux backend.

A phone is Linux with three differences that matter to a tool layer:

* it is **battery powered** - a "vital" that reports only CPU and RAM misses the
  number the user asks about first;
* it is **sandboxed** - a plain app process cannot open the camera, read SMS or
  ask for a location, so those go through the Termux:API bridge
  (:mod:`core.platforms.android.termux`) while everything else is a file read;
* it is **not a desktop** - there is no wallpaper to report, and a screenshot
  needs a permission Android does not give an app (``screencap`` belongs to the
  ``graphics`` group).  Saying so is the correct answer, not a failure.

The point of this backend is that a fresh Termux install with ``python`` and
nothing else still answers every host tool: telemetry comes from ``/proc`` and
``/sys``, not from ``df``/``free`` (which Termux has) and not from
``termux-battery-status`` (which needs a second APK).
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.platforms import common, encoding, shell
from core.platforms.base import PlatformBackend
from core.platforms.android import sysfs
from core.platforms.android import telemetry as _telemetry
from core.platforms.linux import process as _process
from core.platforms.linux.procfs import ProcFS


class AndroidBackend(PlatformBackend):
    scenario = "termux"
    label = "Android/Termux"

    def __init__(self) -> None:
        self._shell_cache: Optional[Tuple[str, List[str]]] = None

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        """Termux ships bash; ``/bin/sh`` is the fallback Android guarantees."""
        if self._shell_cache is None:
            self._shell_cache = shell.resolve(
                is_windows=False, which=shutil.which, environ=os.environ,
                isfile=os.path.isfile)
        return self._shell_cache

    def child_env(self) -> Dict[str, str]:
        return dict(os.environ)

    def output_encodings(self) -> List[str]:
        return ["utf-8"]

    # -- telemetry ---------------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        """Seconds since boot, or ``None`` where Android hides ``/proc/uptime``.

        Android 16 with SELinux denies an app ``/proc/uptime``, ``/proc/stat`` and
        ``/proc/loadavg`` (verified on an OPPO PHY110): the system-wide counters
        are not app-visible, while ``/proc/meminfo`` and a process's own
        ``/proc/<pid>/stat`` are. Reporting "unknown" is the honest answer; a
        fabricated uptime would be indistinguishable from a real one.
        """
        return ProcFS().uptime()

    def vitals(self) -> Dict[str, object]:
        return _telemetry.vitals()

    def info_blocks(self) -> Dict[str, str]:
        return _telemetry.info_blocks()

    def wallpaper(self) -> str:
        return ""  # Android has no per-user wallpaper path readable by an app

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        return _process.alive(pid, start_time)

    def pid_start_time(self, pid: int) -> Optional[int]:
        return _process.start_time_of(pid)

    def kill_tree(self, pid: int) -> None:
        _process.kill_tree(pid)

    # -- phone capabilities ------------------------------------------------

    def clipboard_get(self) -> Optional[str]:
        """Clipboard through ``termux-clipboard-get`` (Android has no /dev entry).

        Android 10+ lets an app *read* the clipboard only while it is the focused
        app (or the default IME). A node running in the background therefore gets
        an **empty** answer even though the clipboard is not empty - verified on
        an OPPO PHY110, where ``READ_CLIPBOARD`` is granted to Termux and the read
        still returns nothing. That is reported as an empty string, not as an
        error: nothing failed, the OS simply hides the value.

        The adapter answers ``{"ok": true, "value": <stdout>}``; a real failure
        carries ``reason`` and comes back as ``None``.
        """
        try:
            from core.platforms.android import termux
        except Exception:  # pragma: no cover - partial checkout
            return None
        result = termux.run_text("termux-clipboard-get", timeout=5.0)
        if isinstance(result, dict) and result.get("ok"):
            return str(result.get("value") or "")
        return None

    def clipboard_set(self, text: str) -> bool:
        try:
            from core.platforms.android import termux
        except Exception:  # pragma: no cover - partial checkout
            return False
        result = termux.run_text("termux-clipboard-set", [str(text)], timeout=5.0)
        return bool(isinstance(result, dict) and result.get("ok"))

    def capabilities(self) -> Dict[str, bool]:
        battery = sysfs.battery() is not None or bool(shutil.which("termux-battery-status"))
        return {
            "shell": True,
            "vitals": os.path.isdir("/proc"),
            "info_blocks": os.path.isdir("/proc"),
            "battery": battery,
            "thermal": bool(sysfs.thermal(limit=1)),
            "process_tree": os.path.isdir("/proc"),
            # Android gives an app no framebuffer access: "screencap" needs the
            # graphics group, and Termux:API has no screen command.
            "screenshot": False,
            "clipboard": bool(shutil.which("termux-clipboard-get")),
        }


__all__ = ["AndroidBackend"]
