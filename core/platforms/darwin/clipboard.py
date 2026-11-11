"""core/platforms/darwin/clipboard.py - the macOS pasteboard.

There is no ``ctypes``-friendly public API for ``NSPasteboard`` without PyObjC,
and pulling PyObjC in would break the "standard library only" rule.  macOS ships
``pbpaste`` and ``pbcopy`` as part of the OS (not as a package the user installs),
so they are the interface here - the same way Windows uses its own DLLs.

Both directions are best effort and never raise: a missing binary or a refused
service (a sandboxed agent) returns ``None`` / ``False`` and the caller reports
"no clipboard" instead of an empty one.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional


def available() -> bool:
    return bool(shutil.which("pbpaste") and shutil.which("pbcopy"))


def get_text() -> Optional[str]:
    """The pasteboard text, or ``None`` when macOS does not hand it over."""
    binary = shutil.which("pbpaste")
    if not binary:
        return None
    try:
        completed = subprocess.run([binary], capture_output=True, timeout=3)
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def set_text(text: str) -> bool:
    """Replace the pasteboard with *text*. False when ``pbcopy`` is unavailable."""
    binary = shutil.which("pbcopy")
    if not binary:
        return False
    try:
        completed = subprocess.run(
            [binary], input=str(text).encode("utf-8"), capture_output=True, timeout=3)
    except Exception:
        return False
    return completed.returncode == 0


__all__ = ["available", "get_text", "set_text"]
