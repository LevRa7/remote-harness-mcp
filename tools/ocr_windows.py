#!/usr/bin/env python3
r"""tools/ocr_windows.py - read the text of a picture with the OCR Windows ships.

Why this exists: Gemini's MCP connector drops ``image`` content blocks (measured on
2026-11-24: a valid block arrives as "the image did not reach me"), so a screenshot
is only useful if its words travel as text. The node can ask any command for that
text - this one uses ``Windows.Media.Ocr``, so it needs no extra program, no admin
right and no language download beyond the OCR languages Windows already has.

    python -m pip install winsdk
    set MESH_OCR_CMD=python D:\path\to\tools\ocr_windows.py

Then ``read_media`` adds the recognised text to every image answer. Usage on its
own: ``python tools/ocr_windows.py <image> [language-tag]`` - it prints the text.
"""

from __future__ import annotations

import asyncio
import sys


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


async def recognise(path: str, language: str = "") -> str:
    """Text of one image through Windows.Media.Ocr (async API, asyncio bridge)."""
    try:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError:
        raise SystemExit(_fail("winsdk is not installed: python -m pip install winsdk"))

    engine = None
    if language:
        engine = OcrEngine.try_create_from_language(language)
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise SystemExit(_fail("Windows has no OCR engine for the profile languages: install a language pack"))

    file = await StorageFile.get_file_from_path_async(path)
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return result.text or ""


def main(argv) -> int:
    # The node reads this process, not a console: say UTF-8 once instead of letting
    # Windows pick the OEM code page for the pipe.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(argv) < 2:
        print("usage: ocr_windows.py <image> [language-tag]", file=sys.stderr)
        return 2
    text = asyncio.run(recognise(argv[1], argv[2] if len(argv) > 2 else ""))
    print(text)
    return 0 if text.strip() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
