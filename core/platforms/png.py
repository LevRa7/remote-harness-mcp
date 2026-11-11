"""core/platforms/png.py - a minimal PNG encoder on top of ``zlib``.

Screenshots must not depend on Pillow: the node ships with the standard library
only, and asking a user to install an image library before they can take a
screenshot defeats the point.  PNG is simple enough to write directly: a header,
one zlib-compressed stream of filter-0 scanlines, and an end chunk, each with a
CRC32.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable, Union

#: Color type 2 = truecolour RGB, bit depth 8.
COLOR_TYPE_RGB = 2


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_rgb(width: int, height: int, rows: Iterable[bytes]) -> bytes:
    """Encode 8-bit RGB scanlines (``width * 3`` bytes each) as a PNG."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    raw = bytearray()
    seen = 0
    for row in rows:
        if len(row) != width * 3:
            raise ValueError("row %d is %d bytes, expected %d" % (seen, len(row), width * 3))
        raw.append(0)  # filter type 0 (None)
        raw.extend(row)
        seen += 1
    if seen != height:
        raise ValueError("got %d rows, expected %d" % (seen, height))

    header = struct.pack(
        ">IIBBBBB", width, height, 8, COLOR_TYPE_RGB, 0, 0, 0
    )
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        _chunk(b"IHDR", header),
        _chunk(b"IDAT", zlib.compress(bytes(raw), 6)),
        _chunk(b"IEND", b""),
    ))


def encode_bgra(width: int, height: int, bgra: Union[bytes, bytearray],
                top_down: bool = True) -> bytes:
    """Encode a 32-bit BGRA buffer (as Windows and X11 hand it out) as PNG.

    Alpha is dropped.  *top_down* is False for the bottom-up layout a DIB
    section uses, which is flipped here so callers do not each have to remember.
    """
    stride = width * 4
    if len(bgra) < stride * height:
        raise ValueError("buffer is %d bytes, expected %d" % (len(bgra), stride * height))

    def row(index: int) -> bytes:
        start = index * stride
        line = bgra[start:start + stride]
        rgb = bytearray(width * 3)
        for x in range(width):
            b, g, r = line[x * 4], line[x * 4 + 1], line[x * 4 + 2]
            rgb[x * 3] = r
            rgb[x * 3 + 1] = g
            rgb[x * 3 + 2] = b
        return bytes(rgb)

    order = range(height) if top_down else range(height - 1, -1, -1)
    return encode_rgb(width, height, (row(index) for index in order))


__all__ = ["COLOR_TYPE_RGB", "encode_bgra", "encode_rgb"]
