"""core/platforms/darwin - the macOS backend.

macOS is POSIX like Linux - same shell, POSIX paths, ``fork``/``signal`` - but it
has none of the ``/proc`` interface the Linux backend reads, so the two cannot
share an implementation. What they DO share is the contract in
:class:`core.platforms.base.PlatformBackend`, which is what every tool calls.

The macOS specific pieces live next to this file:

* :mod:`core.platforms.darwin.process` - liveness, start time and process trees
  through ``libproc`` (the API behind Activity Monitor), plus resident memory;
* :mod:`core.platforms.darwin.telemetry` - RAM, disks, load and uptime from
  ``sysctlbyname`` and ``statfs``, never from ``vm_stat``/``df``/``ps``;
* :mod:`core.platforms.darwin.screen` - a screenshot through CoreGraphics, which
  returns ``NULL`` until the user grants *Screen Recording* (reported as "no
  screenshot", not as a crash);
* :mod:`core.platforms.darwin.clipboard` - the pasteboard through the ``pbcopy``
  and ``pbpaste`` binaries Apple ships with the OS.

On a non-macOS host every one of those degrades to "unavailable" instead of
raising, so the test suite can build this backend anywhere and assert the
contract.
"""

from __future__ import annotations

import locale
import os
import plistlib
import shutil
from typing import Dict, List, Optional, Tuple

from core.platforms import encoding, shell
from core.platforms.base import PlatformBackend
from core.platforms.darwin import clipboard, process as _process, screen, telemetry


def _wallpaper() -> str:
    """The current desktop wallpaper path, or ``""`` when it cannot be named.

    Modern macOS (Sonoma and later) keeps the desktop picture in a binary plist
    under ``Application Support/com.apple.wallpaper``; older releases used the
    ``com.apple.desktop`` defaults domain. ``plistlib`` is in the standard library,
    so this stays dependency-free; an unexpected shape simply answers ``""``.
    """
    candidates = (
        os.path.expanduser("~/Library/Application Support/com.apple.wallpaper/Store/Index.plist"),
        os.path.expanduser("~/Library/Preferences/com.apple.desktop.plist"),
    )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                payload = plistlib.load(handle)
        except Exception:
            continue
        found = _first_image_path(payload)
        if found:
            return found
    return ""


def _first_image_path(node: object, depth: int = 0) -> str:
    """Walk a wallpaper plist for the first ``file://`` URL or image path."""
    if depth > 8:
        return ""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and ("/wallpaper" in value.lower()
                                           or value.lower().endswith((".heic", ".png", ".jpg", ".jpeg"))):
                return value.replace("file://", "")
            found = _first_image_path(value, depth + 1)
            if found:
                return found
    elif isinstance(node, (list, tuple)):
        for value in node:
            found = _first_image_path(value, depth + 1)
            if found:
                return found
    return ""


class DarwinBackend(PlatformBackend):
    scenario = "darwin"
    label = "macOS"

    def __init__(self) -> None:
        self._shell_cache: Optional[Tuple[str, List[str]]] = None
        self._encodings: Optional[List[str]] = None

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        if self._shell_cache is None:
            self._shell_cache = shell.resolve(
                is_windows=False, which=shutil.which, environ=os.environ,
                isfile=os.path.isfile)
        return self._shell_cache

    def child_env(self) -> Dict[str, str]:
        return dict(os.environ)

    def output_encodings(self) -> List[str]:
        if self._encodings is None:
            try:
                preferred = locale.getpreferredencoding(False)
            except Exception:
                preferred = ""
            self._encodings = encoding.candidate_encodings(
                is_windows=False, preferred=preferred)
        return self._encodings

    # -- telemetry ---------------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        return telemetry.uptime_seconds()

    def vitals(self) -> Dict[str, object]:
        return telemetry.vitals()

    def info_blocks(self) -> Dict[str, str]:
        return telemetry.info_blocks()

    def wallpaper(self) -> str:
        return _wallpaper()

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        return _process.alive(pid, start_time)

    def pid_start_time(self, pid: int) -> Optional[int]:
        return _process.start_time_of(pid)

    def kill_tree(self, pid: int) -> None:
        _process.kill_tree(pid)

    # -- desktop capabilities ---------------------------------------------

    def screenshot_png(self) -> Optional[bytes]:
        return screen.screenshot_png()

    def clipboard_get(self) -> Optional[str]:
        return clipboard.get_text()

    def clipboard_set(self, text: str) -> bool:
        return clipboard.set_text(text)

    def capabilities(self) -> Dict[str, bool]:
        # ``process_tree`` does not depend on RAM: libproc answers as long as the
        # binary runs on macOS, so the honest answer is "this is macOS", not "the
        # memory counters answered".
        is_macos = os.uname().sysname == "Darwin" if hasattr(os, "uname") else False
        return {
            "shell": True,
            "vitals": bool(telemetry.total_bytes()),
            "info_blocks": bool(telemetry.total_bytes()),
            "process_tree": is_macos,
            "screenshot": screen.available(),
            "clipboard": clipboard.available(),
        }


__all__ = ["DarwinBackend"]
