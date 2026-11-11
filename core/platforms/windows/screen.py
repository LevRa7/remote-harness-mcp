"""core/platforms/windows/screen.py - a screenshot through GDI, with no helper exe.

The usual way to screenshot Windows is to shell out to PowerShell and
``System.Drawing``, which costs a second of startup, needs the .NET Framework
and a PowerShell that policy has not disabled.  GDI does it directly:

1. become per-monitor DPI aware, otherwise a 150% display is captured as a
   blurry upscaled 1280x720 instead of its real pixels;
2. read the virtual-screen rectangle (all monitors, not just the primary);
3. create a top-down 32-bit DIB section and ``BitBlt`` the screen into it;
4. encode the BGRA buffer as PNG with :mod:`core.platforms.png`.

Nothing is left to a program on ``PATH``: ``user32``/``gdi32`` are part of the
OS.
"""

from __future__ import annotations

import ctypes
from typing import Optional, Tuple

from core.platforms import png
from core.platforms.windows import winapi


def _become_dpi_aware() -> None:
    """Best effort: per-monitor-v2, then system-aware, then give up quietly."""
    try:
        if winapi.SetProcessDpiAwarenessContext is not None:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
            if winapi.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
    except Exception:
        pass
    try:
        if winapi.SetProcessDPIAware is not None:
            winapi.SetProcessDPIAware()
    except Exception:
        pass


def virtual_screen() -> Tuple[int, int, int, int]:
    """``(x, y, width, height)`` of the whole desktop across every monitor."""
    if winapi.GetSystemMetrics is None:
        return (0, 0, 0, 0)
    x = winapi.GetSystemMetrics(winapi.SM_XVIRTUALSCREEN)
    y = winapi.GetSystemMetrics(winapi.SM_YVIRTUALSCREEN)
    width = winapi.GetSystemMetrics(winapi.SM_CXVIRTUALSCREEN)
    height = winapi.GetSystemMetrics(winapi.SM_CYVIRTUALSCREEN)
    if width <= 0 or height <= 0:
        return (0, 0, 0, 0)
    return (x, y, width, height)


def screenshot_png() -> Optional[bytes]:
    """The desktop as PNG bytes, or ``None`` when there is no accessible desktop.

    ``None`` is the honest answer for a service session (session 0), a locked
    workstation without access, or a headless CI runner - the caller reports
    "no desktop to capture" instead of a black rectangle.
    """
    if winapi.GetDC is None or winapi.CreateDIBSection is None:
        return None
    _become_dpi_aware()
    x, y, width, height = virtual_screen()
    if width <= 0 or height <= 0:
        return None

    screen_dc = winapi.GetDC(None)
    if not screen_dc:
        return None
    memory_dc = winapi.CreateCompatibleDC(screen_dc)
    if not memory_dc:
        winapi.ReleaseDC(None, screen_dc)
        return None

    bits = ctypes.c_void_p()
    info = winapi.BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(winapi.BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # negative: top-down rows, no flip later
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = winapi.BI_RGB

    bitmap = winapi.CreateDIBSection(
        screen_dc, ctypes.byref(info), winapi.DIB_RGB_COLORS,
        ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return None
        previous = winapi.SelectObject(memory_dc, bitmap)
        try:
            if not winapi.BitBlt(memory_dc, 0, 0, width, height,
                                 screen_dc, x, y, winapi.SRCCOPY):
                return None
            buffer = ctypes.string_at(bits, width * height * 4)
        finally:
            winapi.SelectObject(memory_dc, previous)
        return png.encode_bgra(width, height, buffer, top_down=True)
    finally:
        if bitmap:
            winapi.DeleteObject(bitmap)
        winapi.DeleteDC(memory_dc)
        winapi.ReleaseDC(None, screen_dc)


__all__ = ["screenshot_png", "virtual_screen"]
