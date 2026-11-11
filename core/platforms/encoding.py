"""core/platforms/encoding.py - decoding captured child output.

A captured pipe carries bytes, and one Windows host can see three encodings in
one session:

* the **OEM console code page** - what ``cmd.exe`` and PowerShell themselves
  write (cp866 on a Russian box, cp437 elsewhere);
* the **ANSI code page** - what a native tool using the ANSI API writes
  (cp1251 here);
* **UTF-8** - what git, node, curl and a Python child told to speak UTF-8 write.

There is no in-band marker, and every single-byte code page decodes every byte,
so this is a priority list rather than a real fallback chain.  Getting the order
wrong is what turned cp866 ``echo привет`` into ``ЇаЁўҐв`` and UTF-8 into
``РїСЂРёРІРµС‚``.
"""

from __future__ import annotations

import locale
from typing import Callable, List, Optional

#: How many leading candidates the caller may pass before the list is deduped.
MAX_CANDIDATES = 8


def candidate_encodings(*, is_windows: bool,
                        code_pages: Optional[Callable[[], List[str]]] = None,
                        preferred: Optional[str] = None) -> List[str]:
    """Encodings to try, most likely first.

    * ``utf-8`` always leads: a UTF-8 decode that *succeeds* is strong evidence,
      and every non-UTF-8 producer above fails it rather than producing text.
    * On Windows the OEM code page is next, then the ANSI one.
    * Elsewhere the locale's preferred encoding is the only realistic second
      candidate (a Latin-1 or KOI8 terminal).
    """
    candidates: List[str] = ["utf-8"]
    if is_windows:
        pages = []
        if callable(code_pages):
            try:
                pages = list(code_pages() or [])
            except Exception:
                pages = []
        candidates.extend(pages)
    else:
        encoding = preferred
        if not encoding:
            try:
                encoding = locale.getpreferredencoding(False)
            except Exception:
                encoding = ""
        if encoding:
            candidates.append(encoding)

    unique: List[str] = []
    seen = set()
    for name in candidates:
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return unique[:MAX_CANDIDATES]


def decode(data, encodings: List[str]) -> str:
    """Decode *data* with the first encoding that fits, normalising newlines.

    ``\\r\\n`` becomes ``\\n`` because pagination cursors are character offsets
    into the returned text: letting a Windows child's CRLF through would make
    the same output paginate differently on two hosts.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data.replace("\r\n", "\n").replace("\r", "\n")
    if not data:
        return ""
    for encoding in encodings or ["utf-8"]:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


__all__ = ["MAX_CANDIDATES", "candidate_encodings", "decode"]
