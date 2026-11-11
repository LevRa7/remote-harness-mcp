"""core/platforms/windows/clipboard.py - clipboard text through user32.

Reading the clipboard needs ``OpenClipboard`` to succeed, and another process
holding it is normal (a clipboard manager, a slow app).  Both directions
therefore retry a few times instead of reporting the clipboard as empty: an
empty answer that is really a lock is the kind of wrong the model cannot
detect.
"""

from __future__ import annotations

import ctypes
import time
from typing import Optional

from core.platforms.windows import winapi

#: How many times to retry ``OpenClipboard`` and how long to wait between tries.
OPEN_ATTEMPTS = 8
OPEN_DELAY_SECONDS = 0.02


def _open() -> bool:
    if winapi.OpenClipboard is None:
        return False
    for _ in range(OPEN_ATTEMPTS):
        try:
            if winapi.OpenClipboard(None):
                return True
        except Exception:  # pragma: no cover - defensive
            return False
        time.sleep(OPEN_DELAY_SECONDS)
    return False


def get_text() -> Optional[str]:
    """Clipboard text, or ``None`` when the clipboard holds no text."""
    if not _open():
        return None
    try:
        if not winapi.IsClipboardFormatAvailable(winapi.CF_UNICODETEXT):
            return None
        handle = winapi.GetClipboardData(winapi.CF_UNICODETEXT)
        if not handle:
            return None
        pointer = winapi.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            winapi.GlobalUnlock(handle)
    finally:
        try:
            winapi.CloseClipboard()
        except Exception:  # pragma: no cover
            pass


def set_text(text: str) -> bool:
    """Replace the clipboard with *text*. False when the clipboard is locked."""
    if not _open():
        return False
    try:
        if not winapi.EmptyClipboard():
            return False
        encoded = (text + "\0").encode("utf-16-le")
        handle = winapi.GlobalAlloc(winapi.GMEM_MOVEABLE, len(encoded))
        if not handle:
            return False
        pointer = winapi.GlobalLock(handle)
        if not pointer:
            winapi.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, encoded, len(encoded))
        finally:
            winapi.GlobalUnlock(handle)
        if not winapi.SetClipboardData(winapi.CF_UNICODETEXT, handle):
            winapi.GlobalFree(handle)
            return False
        # Ownership passed to the clipboard on success: do not free it.
        return True
    finally:
        try:
            winapi.CloseClipboard()
        except Exception:  # pragma: no cover
            pass


__all__ = ["get_text", "set_text"]
