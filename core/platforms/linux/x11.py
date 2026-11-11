"""core/platforms/linux/x11.py - a real screenshot on Linux through libX11.

Linux has no single "screen" API: a headless server has none at all, a Wayland
session refuses ``XGetImage`` on other clients' windows, and an X11 session
answers the classic way.  This module is the X11 path, through ``ctypes`` and the
system's own ``libX11`` - no ``import``, no ``scrot``, no ``gnome-screenshot``:

1. ``XOpenDisplay(NULL)`` - fails cleanly when ``DISPLAY`` points nowhere;
2. ``XGetImage`` on the root window of the default screen;
3. read the ``XImage`` the server allocated (width, height, stride, masks) and
   turn it into RGB rows;
4. encode those rows as PNG with :mod:`core.platforms.png`.

The pixel conversion has a fast path for the layout every TrueColor X server
actually serves (32 bits per pixel, BGRX, little-endian, 8 bits per channel):
slicing a ``memoryview`` runs in C, while a per-pixel Python loop over a 4K
screen would take tens of seconds.  Exotic visuals fall back to the mask-driven
loop, which is correct but slow - and a screenshot that is merely slow is still
better than none.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import List, Optional, Sequence, Tuple

from core.platforms import png

#: ``ZPixmap`` - the format XGetImage documents for a client-visible image.
ZPIXMAP = 2
#: ``AllPlanes`` (~0UL): take every plane, not a subset.
ALL_PLANES = ctypes.c_ulong(-1).value
#: ``LSBFirst`` / ``MSBFirst`` as the XImage field encodes them.
LSB_FIRST = 0

#: The two layouts the fast path understands: 0xRRGGBB in a 32- or 24-bit pixel.
_RGB_MASKS = (0xFF0000, 0xFF00, 0xFF)

_LIB = None
_LIB_LOADED = False


class XImage(ctypes.Structure):
    """The leading fields of ``XImage`` that a client may read.

    ``Xlib.h`` continues with ``obdata`` and a struct of function pointers; they
    are omitted because this code never touches them (destruction goes through
    ``XDestroyImage``/``_XDestroyImage``, which knows the real size).
    """

    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]


def load():
    """The X11 client library, or ``None`` when this host has no X client lib."""
    global _LIB, _LIB_LOADED
    if not _LIB_LOADED:
        _LIB_LOADED = True
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        try:
            _LIB = ctypes.CDLL(name)
        except OSError:
            _LIB = None
    return _LIB


def available() -> bool:
    """True when a screenshot has a chance: an X display and its client library."""
    return bool(os.environ.get("DISPLAY")) and load() is not None


def _channel(value: int, mask: int) -> int:
    """Scale the bits selected by *mask* to 0..255 (0 when the mask is empty)."""
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    maximum = mask >> shift
    if maximum <= 0:
        return 0
    scaled = ((value & mask) >> shift) * 255 // maximum
    return 255 if scaled > 255 else scaled


def _fast_layout(bits_per_pixel: int, byte_order: int,
                 masks: Tuple[int, int, int]) -> bool:
    return (byte_order == LSB_FIRST and masks == _RGB_MASKS
            and bits_per_pixel in (24, 32))


def image_rows(width: int, height: int, bytes_per_line: int,
               bits_per_pixel: int, byte_order: int,
               red_mask: int, green_mask: int, blue_mask: int,
               data: bytes) -> List[bytes]:
    """Convert raw ``XImage`` bytes into RGB scanlines (``width * 3`` each).

    *byte_order* is the XImage constant: ``0`` = LSBFirst, ``1`` = MSBFirst.
    Raises ``ValueError`` for a pixel size the decoder does not understand
    (1-bit or 8-bit palette visuals); the caller then reports "cannot capture"
    instead of shipping garbage.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    step = bits_per_pixel // 8
    if step not in (2, 3, 4):
        raise ValueError("unsupported %d bits per pixel" % bits_per_pixel)
    if len(data) < bytes_per_line * height:
        raise ValueError("image buffer is shorter than bytes_per_line * height")

    masks = (red_mask, green_mask, blue_mask)
    if _fast_layout(bits_per_pixel, byte_order, masks):
        rows: List[bytes] = []
        bpp = step
        for y in range(height):
            start = y * bytes_per_line
            line = memoryview(data)[start:start + width * bpp]
            rgb = bytearray(width * 3)
            if bpp == 4:
                # BGRX: bytes 2,1,0 of every 4-byte pixel are R,G,B.
                rgb[0::3] = line[2::4]
                rgb[1::3] = line[1::4]
                rgb[2::3] = line[0::4]
            else:
                # BGR: bytes 2,1,0 of every 3-byte pixel are R,G,B.
                rgb[0::3] = line[2::3]
                rgb[1::3] = line[1::3]
                rgb[2::3] = line[0::3]
            rows.append(bytes(rgb))
        return rows

    order = "little" if byte_order == LSB_FIRST else "big"
    rows = []
    for y in range(height):
        start = y * bytes_per_line
        line = data[start:start + width * step]
        rgb = bytearray(width * 3)
        for x in range(width):
            value = int.from_bytes(line[x * step:(x + 1) * step], order)
            index = x * 3
            rgb[index] = _channel(value, red_mask)
            rgb[index + 1] = _channel(value, green_mask)
            rgb[index + 2] = _channel(value, blue_mask)
        rows.append(bytes(rgb))
    return rows


def _destroy(lib, image) -> None:
    """Free the XImage. ``XDestroyImage`` is a macro; ``_XDestroyImage`` is the symbol."""
    for name in ("XDestroyImage", "_XDestroyImage"):
        function = getattr(lib, name, None)
        if function is not None:
            try:
                function.argtypes = [ctypes.POINTER(XImage)]
                function.restype = None
                function(image)
            except Exception:  # pragma: no cover - defensive
                pass
            return


def _prototype(lib) -> None:
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XCloseDisplay.restype = ctypes.c_int
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultScreen.restype = ctypes.c_int
    lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
    lib.XDisplayWidth.restype = ctypes.c_int
    lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XDisplayHeight.restype = ctypes.c_int
    lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XGetImage.restype = ctypes.POINTER(XImage)
    lib.XGetImage.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
    ]


def screenshot_png() -> Optional[bytes]:
    """The X11 root window as PNG bytes, or ``None`` (headless/Wayland/refused)."""
    if not available():
        return None
    lib = load()
    if lib is None:  # pragma: no cover - available() already checked
        return None
    try:
        _prototype(lib)
        display = lib.XOpenDisplay(None)
        if not display:
            return None
        try:
            screen = lib.XDefaultScreen(display)
            width = lib.XDisplayWidth(display, screen)
            height = lib.XDisplayHeight(display, screen)
            if width <= 0 or height <= 0:
                return None
            root = lib.XDefaultRootWindow(display)
            image = lib.XGetImage(display, root, 0, 0, width, height,
                                  ALL_PLANES, ZPIXMAP)
            if not image:
                return None  # a Wayland session, a compositor refusal, a locked screen
            try:
                info = image.contents
                if info.format != ZPIXMAP or info.data is None:
                    return None
                raw = ctypes.string_at(info.data, info.bytes_per_line * info.height)
                rows = image_rows(
                    info.width, info.height, info.bytes_per_line,
                    info.bits_per_pixel, info.byte_order,
                    int(info.red_mask), int(info.green_mask), int(info.blue_mask),
                    raw)
            finally:
                _destroy(lib, image)
            return png.encode_rgb(width, height, rows)
        finally:
            lib.XCloseDisplay(display)
    except Exception:
        return None


__all__ = [
    "ALL_PLANES", "LSB_FIRST", "XImage", "ZPIXMAP", "available", "image_rows",
    "load", "screenshot_png",
]
