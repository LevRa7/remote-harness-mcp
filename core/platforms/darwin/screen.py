"""core/platforms/darwin/screen.py - a screenshot through CoreGraphics.

macOS has no framebuffer device to read: the compositor hands out a
``CGImageRef`` and only to a process the user has allowed to *record the screen*
(System Settings -> Privacy & Security -> Screen Recording).  This module asks
the system directly - no ``screencapture`` binary, no ``osascript``:

1. ``CGMainDisplayID()``;
2. ``CGDisplayCreateImage(id)`` - returns ``NULL`` when the permission is missing,
   which is the normal first-run state and is reported as "no screenshot";
3. ``CGImageGetDataProvider`` -> ``CGDataProviderCopyData`` -> the pixels;
4. encode them as PNG with :mod:`core.platforms.png`.

The bytes CoreGraphics produces for a display image are 32-bit BGRA with
premultipled alpha (``kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little``),
which is exactly what :func:`core.platforms.png.encode_bgra` expects.
"""

from __future__ import annotations

import ctypes
from typing import Optional

from core.platforms import png

_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
_COREFOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

#: ``kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little``.
_BGRA_LITTLE = 0x1 | 0x2000

_CG = None
_CF = None
_LOADED = False


def _load():
    """Load the frameworks once; ``None`` on any non-macOS host."""
    global _CG, _CF, _LOADED
    if not _LOADED:
        _LOADED = True
        try:
            _CG = ctypes.CDLL(_GRAPHICS)
        except OSError:
            _CG = None
        try:
            _CF = ctypes.CDLL(_COREFOUNDATION)
        except OSError:
            _CF = None
    return _CG, _CF


def available() -> bool:
    return _load()[0] is not None


def screenshot_png() -> Optional[bytes]:
    """The main display as PNG, or ``None`` without a display or the permission."""
    cg, cf = _load()
    if cg is None:
        return None
    try:
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGMainDisplayID.argtypes = []
        cg.CGDisplayCreateImage.restype = ctypes.c_void_p
        cg.CGDisplayCreateImage.argtypes = [ctypes.c_uint32]
        cg.CGImageGetWidth.restype = ctypes.c_size_t
        cg.CGImageGetWidth.argtypes = [ctypes.c_void_p]
        cg.CGImageGetHeight.restype = ctypes.c_size_t
        cg.CGImageGetHeight.argtypes = [ctypes.c_void_p]
        cg.CGImageGetBytesPerRow.restype = ctypes.c_size_t
        cg.CGImageGetBytesPerRow.argtypes = [ctypes.c_void_p]
        cg.CGImageGetBitsPerPixel.restype = ctypes.c_size_t
        cg.CGImageGetBitsPerPixel.argtypes = [ctypes.c_void_p]
        cg.CGImageGetBitmapInfo.restype = ctypes.c_uint32
        cg.CGImageGetBitmapInfo.argtypes = [ctypes.c_void_p]
        cg.CGImageGetDataProvider.restype = ctypes.c_void_p
        cg.CGImageGetDataProvider.argtypes = [ctypes.c_void_p]
        cg.CGDataProviderCopyData.restype = ctypes.c_void_p
        cg.CGDataProviderCopyData.argtypes = [ctypes.c_void_p]
        cg.CGImageRelease.restype = None
        cg.CGImageRelease.argtypes = [ctypes.c_void_p]
        cf.CFDataGetBytePtr.restype = ctypes.c_void_p
        cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
        cf.CFDataGetLength.restype = ctypes.c_long
        cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        image = cg.CGDisplayCreateImage(cg.CGMainDisplayID())
        if not image:
            return None  # no screen-recording permission, or a locked session
        try:
            width = int(cg.CGImageGetWidth(image))
            height = int(cg.CGImageGetHeight(image))
            stride = int(cg.CGImageGetBytesPerRow(image))
            bpp = int(cg.CGImageGetBitsPerPixel(image))
            info = int(cg.CGImageGetBitmapInfo(image))
            if width <= 0 or height <= 0 or bpp != 32 or stride < width * 4:
                return None
            provider = cg.CGImageGetDataProvider(image)
            if not provider:
                return None
            data_ref = cg.CGDataProviderCopyData(provider)
            if not data_ref:
                return None
            try:
                length = int(cf.CFDataGetLength(data_ref))
                pointer = cf.CFDataGetBytePtr(data_ref)
                if not pointer or length < stride * height:
                    return None
                raw = ctypes.string_at(pointer, length)
            finally:
                cf.CFRelease(data_ref)
        finally:
            cg.CGImageRelease(image)

        # A stride wider than the image is normal (64-byte alignment): crop each row.
        if stride == width * 4:
            buffer = raw
        else:
            buffer = b"".join(raw[y * stride:y * stride + width * 4] for y in range(height))
        # The display image is BGRA with premultiplied alpha; alpha is dropped and
        # the first three bytes of every pixel are already in that order.
        return png.encode_bgra(width, height, buffer, top_down=True)
    except Exception:
        return None


__all__ = ["available", "screenshot_png"]
