"""core/platforms/raster.py - read, shrink and re-write a raster image with the
standard library only.

A picture handed to a model travels as base64 inside a JSON document, so every
pixel is paid for twice: once as bytes and once more as the 4/3 base64 expansion.
A full-size desktop screenshot (2520x1680 PNG) is ~230 KB on disk and ~310 KB on
the wire; a phone photo is several megabytes and will either hit a transport
limit or push a real conversation out of the context window. Something has to
downscale, and the node cannot assume Pillow: ``core/platforms/png.py`` already
*writes* PNG through ``zlib`` alone for exactly that reason (a screenshot must
work before anyone installs an image library), so this module adds the other
half - read a PNG back, shrink it, write it again.

What is deliberately missing: a JPEG codec. A baseline JPEG decoder is a Huffman
decoder plus an inverse DCT plus chroma upsampling, and a matching encoder is
another few hundred lines that would be exercised only by photographs. The
honest division is therefore:

* PNG (the format this project produces itself, and the format of every
  screenshot) is decoded and resized here, in pure Python;
* JPEG/GIF/WebP are only *measured* here (a header parse, no pixels) and are
  handed to ffmpeg when the host has one - ``core/media.py`` owns that call.

Everything here answers ``None``/raises :class:`RasterError` instead of guessing,
so a caller can fall back to sending the original bytes or to saying why.
"""

from __future__ import annotations

import array as _array
import struct
import zlib
from typing import List, Optional, Sequence, Tuple

from core.platforms import png as _png

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: PNG colour type -> bytes per pixel at bit depth 8.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class RasterError(Exception):
    """The bytes are an image this module will not decode.

    Raised for a truncated file, a 16-bit or interlaced PNG, or an unknown colour
    type - the cases where a "best effort" guess would silently produce garbage
    pixels. The caller keeps the original bytes instead.
    """


def png_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from the IHDR chunk, without decoding any pixels."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except Exception:  # pragma: no cover - length is checked above
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _chunks(data: bytes):
    """Yield (kind, payload) for every PNG chunk, stopping at a short read."""
    pos = len(PNG_SIGNATURE)
    total = len(data)
    while pos + 8 <= total:
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end > total:
            return
        yield kind, data[start:end]
        pos = end + 4  # skip the CRC


def _unfilter(kind: int, line: bytearray, prev: bytes, bpp: int) -> None:
    """Reverse one PNG scanline filter in place.

    The five filters are defined by the PNG spec; ``bpp`` is the distance to the
    pixel on the left (bytes per pixel, not bits - bit depth 8 only).
    """
    if kind == 0:
        return
    n = len(line)
    if kind == 1:  # Sub: the pixel to the left
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 0xFF
        return
    if kind == 2:  # Up: the pixel above
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 0xFF
        return
    if kind == 3:  # Average of left and above
        for i in range(n):
            above = prev[i]
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + above) >> 1)) & 0xFF
        return
    if kind == 4:  # Paeth
        for i in range(n):
            above = prev[i]
            left = line[i - bpp] if i >= bpp else 0
            upper_left = prev[i - bpp] if i >= bpp else 0
            estimate = left + above - upper_left
            pa = abs(estimate - left)
            pb = abs(estimate - above)
            pc = abs(estimate - upper_left)
            predictor = left if (pa <= pb and pa <= pc) else (above if pb <= pc else upper_left)
            line[i] = (line[i] + predictor) & 0xFF
        return
    raise RasterError("unknown PNG filter type %r" % (kind,))


def decode_png(data: bytes) -> Tuple[int, int, List[bytearray]]:
    """Decode a PNG into (width, height, rows) with 3 bytes (RGB) per pixel.

    Alpha is composited onto white rather than dropped: a screenshot of a window
    with rounded corners has transparent pixels around it, and "black" there reads
    as a rendering bug to whoever looks at the result.

    Supports bit depth 8 and the non-interlaced colour types 0 (grey), 2 (RGB),
    3 (palette), 4 (grey+alpha) and 6 (RGBA) - the set every screenshot tool and
    PNG optimiser emits. Anything else raises :class:`RasterError`; the caller
    then sends the original file instead of a distorted one.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise RasterError("not a PNG")
    width = height = 0
    depth = 0
    colour = -1
    interlace = 0
    palette = b""
    transparency: Optional[bytes] = None
    idat = bytearray()
    for kind, payload in _chunks(data):
        if kind == b"IHDR":
            if len(payload) < 13:
                raise RasterError("truncated PNG header")
            width, height, depth, colour, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload[:13])
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise RasterError("PNG has no usable header")
    if depth != 8:
        raise RasterError("unsupported PNG bit depth %d (only 8 is decoded)" % depth)
    if interlace:
        raise RasterError("interlaced PNG is not decoded")
    channels = _CHANNELS.get(colour)
    if channels is None:
        raise RasterError("unsupported PNG colour type %r" % (colour,))
    if not idat:
        raise RasterError("PNG carries no pixel data")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise RasterError("PNG pixel stream is corrupt: %s" % exc)
    stride = width * channels
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise RasterError("PNG pixel stream is short (%d of %d bytes)" % (len(raw), expected))

    scanlines: List[bytearray] = []
    prev = bytes(stride)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        _unfilter(filter_type, line, prev, channels)
        scanlines.append(line)
        prev = bytes(line)

    return width, height, _to_rgb(width, height, colour, scanlines, palette, transparency)


def _to_rgb(width: int, height: int, colour: int, scanlines: Sequence[bytearray],
            palette: bytes, transparency: Optional[bytes]) -> List[bytearray]:
    """Convert decoded scanlines to RGB rows (3 bytes per pixel)."""
    if colour == 2:
        return list(scanlines)
    rows: List[bytearray] = []
    if colour in (0, 4):
        step = 1 if colour == 0 else 2
        for line in scanlines:
            out = bytearray(width * 3)
            for x in range(width):
                base = x * step
                grey = line[base]
                alpha = 255 if step == 1 else line[base + 1]
                value = (grey * alpha + 255 * (255 - alpha)) // 255 if alpha != 255 else grey
                out[x * 3] = out[x * 3 + 1] = out[x * 3 + 2] = value
            rows.append(out)
        return rows
    if colour == 6:
        for line in scanlines:
            out = bytearray(width * 3)
            for x in range(width):
                base = x * 4
                alpha = line[base + 3]
                if alpha == 255:
                    out[x * 3] = line[base]
                    out[x * 3 + 1] = line[base + 1]
                    out[x * 3 + 2] = line[base + 2]
                else:
                    out[x * 3] = (line[base] * alpha + 255 * (255 - alpha)) // 255
                    out[x * 3 + 1] = (line[base + 1] * alpha + 255 * (255 - alpha)) // 255
                    out[x * 3 + 2] = (line[base + 2] * alpha + 255 * (255 - alpha)) // 255
            rows.append(out)
        return rows
    # Colour type 3: one palette index per pixel.
    if colour != 3:  # pragma: no cover - guarded by _CHANNELS
        raise RasterError("unsupported PNG colour type %r" % (colour,))
    if len(palette) < 3:
        raise RasterError("palette PNG without a PLTE chunk")
    entries = len(palette) // 3
    for line in scanlines:
        out = bytearray(width * 3)
        for x in range(width):
            index = line[x]
            if index >= entries:
                index = 0
            out[x * 3] = palette[index * 3]
            out[x * 3 + 1] = palette[index * 3 + 1]
            out[x * 3 + 2] = palette[index * 3 + 2]
            if transparency is not None and index < len(transparency):
                alpha = transparency[index]
                if alpha != 255:
                    out[x * 3] = (out[x * 3] * alpha + 255 * (255 - alpha)) // 255
                    out[x * 3 + 1] = (out[x * 3 + 1] * alpha + 255 * (255 - alpha)) // 255
                    out[x * 3 + 2] = (out[x * 3 + 2] * alpha + 255 * (255 - alpha)) // 255
        rows.append(out)
    return rows


def encode_png(width: int, height: int, rows: Sequence[bytes]) -> bytes:
    """Encode RGB rows as a PNG (delegates to the project encoder)."""
    return _png.encode_rgb(width, height, rows)


def scale_factor(width: int, height: int, max_side: int) -> int:
    """Integer downscale factor that keeps both sides within *max_side*.

    An integer factor is what makes the shrink cheap and exact: every destination
    pixel is the average of exactly ``factor x factor`` source pixels, with no
    fractional bookkeeping in the inner loop.
    """
    longest = max(width, height)
    if max_side <= 0 or longest <= max_side:
        return 1
    factor = (longest + max_side - 1) // max_side
    return max(1, factor)


def shrink_rgb(width: int, height: int, rows: Sequence[bytes],
               max_side: int) -> Tuple[int, int, List[bytearray]]:
    """Box-filter *rows* down by an integer factor so both sides fit *max_side*."""
    factor = scale_factor(width, height, max_side)
    if factor <= 1:
        return width, height, [bytearray(row) for row in rows]
    out_w = max(1, width // factor)
    out_h = max(1, height // factor)

    # Horizontal pass: average ``factor`` neighbouring pixels of every source row.
    reduced: List[bytearray] = []
    span = factor * 3
    for row in rows:
        out = bytearray(out_w * 3)
        for x in range(out_w):
            start = x * span
            red = green = blue = 0
            for offset in range(0, span, 3):
                index = start + offset
                red += row[index]
                green += row[index + 1]
                blue += row[index + 2]
            target = x * 3
            out[target] = red // factor
            out[target + 1] = green // factor
            out[target + 2] = blue // factor
        reduced.append(out)

    # Vertical pass: average ``factor`` consecutive reduced rows.
    total = factor * factor
    result: List[bytearray] = []
    width_bytes = out_w * 3
    # An unsigned-short array holds the running sum of up to 257 rows
    # (255 * 257 < 65536); a bytearray would overflow on the second row.
    for y in range(out_h):
        accumulator = _array.array("H", [0]) * width_bytes
        for step in range(factor):
            source = reduced[(y * factor) + step]
            for index in range(width_bytes):
                accumulator[index] += source[index]
        result.append(bytearray(value // total for value in accumulator))
    return out_w, out_h, result


def jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from a JPEG SOF marker, without decoding the image."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    pos = 2
    total = len(data)
    while pos + 4 <= total:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            pos += 2 if marker != 0xFF else 1
            continue
        if marker == 0xD9:  # EOI before any SOF
            return None
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if length < 2:
            return None
        # SOF0..SOF15 except the four markers that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > total:
                return None
            height, width = struct.unpack(">HH", data[pos + 5:pos + 9])
            if width <= 0 or height <= 0:
                return None
            return width, height
        pos += 2 + length
    return None


def gif_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from a GIF logical screen descriptor."""
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width, height = struct.unpack("<HH", data[6:10])
    if width <= 0 or height <= 0:
        return None
    return width, height


def webp_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) from a WebP header (lossy, lossless and extended)."""
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    try:
        if chunk == b"VP8 " and len(data) >= 30:
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            bits = struct.unpack("<I", data[21:25])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return width, height
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def image_size(data: bytes) -> Optional[Tuple[str, int, int]]:
    """(mime, width, height) for the formats this module can measure."""
    for sniff, mime in ((png_size, "image/png"), (jpeg_size, "image/jpeg"),
                        (gif_size, "image/gif"), (webp_size, "image/webp")):
        size = sniff(data)
        if size:
            return mime, size[0], size[1]
    return None
