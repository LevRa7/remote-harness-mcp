"""core/media.py - turn one file on the node into the MCP content blocks a client
can actually show.

``read_file`` answers with text. That is the right shape for source code and the
wrong shape for everything a user really hands over: a screenshot, a photo, a PDF,
a voice memo, a clip. The Model Context Protocol has a block for each of those -
``image`` (base64 + ``mimeType``), ``audio`` (the same) and ``text`` for anything
that has to stay text - and this module is the single place that decides which
block a file becomes, how much of it fits, and what to say when it does not fit
at all.

Design rules
------------

* **Standard library only.** ffmpeg/ffprobe, pdftotext, PyMuPDF and pypdf are
  *optional accelerators*: each is looked up when it is needed and a missing one
  changes the answer, never breaks the call. A bare node still sends text, PNG and
  PDF text; a node with ffmpeg also sends video frames and shrunk photos.
* **Budgets are explicit and reported.** Base64 is ~33% larger than the bytes it
  carries, and a tool response is read by a model with a context window, so every
  call is capped (file size, per-image bytes, total payload, characters) and every
  cut is named in the text block - the caller is told what was left out and how to
  ask for it (``cursor`` for text, ``page_start`` for a PDF).
* **Binary never leaks into text.** A file that is not decodable text is never
  returned as mojibake: the module either wraps it in a real block or explains,
  with the reason, what to install or which tool to use instead.
* **The answer is a list of blocks.** ``content`` is a complete MCP content array
  and both transports (``core/server.py`` and the gateway) forward it verbatim.

Public interface::

    read_media(path, **options) -> dict    # {"ok": True, "content": [...], ...}
    available_backends() -> dict
"""

from __future__ import annotations

import base64
import codecs
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave
import zlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import paths
from core.platforms import raster

#: A file is read whole before it is inspected, so the cap protects the node's
#: memory as much as the client's context. 30 MiB covers a long PDF or an office
#: document; anything larger is a job for share_file instead.
DEFAULT_MAX_BYTES = 30 * 1024 * 1024

#: Characters of text returned in one call (a page of a big log or a PDF).
DEFAULT_MAX_CHARS = 24000

#: Longest side of a downscaled image: enough to read a screenshot or a slide,
#: small enough that the base64 stays inside a conversation.
DEFAULT_MAX_SIDE = 1280

#: Bytes of one encoded image block before it is worth downscaling further.
DEFAULT_MAX_IMAGE_BYTES = 700 * 1024

#: Base64 characters of media in ONE response (~2 MB of text). Every block is
#: counted before it is added, so an eight-frame video cannot quietly become a
#: 20 MB answer.
DEFAULT_MAX_TOTAL_B64 = 2_600_000

#: An audio block is only sent when the caller asks for it *and* the file fits:
#: a node cannot know whether the model takes audio, so the default is the honest
#: one - metadata plus a transcript hint.
DEFAULT_MAX_AUDIO_BYTES = 1024 * 1024

#: Frames from a video, or page images from a scanned PDF.
DEFAULT_MAX_FRAMES = 4

#: A converted frame or page must not hang the tool call: the whole point of the
#: 28 s tunnel budget in the gateway is that a tool answers before the client gives
#: up, so each external command gets half of it.
COMMAND_TIMEOUT = 14.0

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_PDF = "pdf"
KIND_AUDIO = "audio"
KIND_VIDEO = "video"
KIND_ARCHIVE = "archive"
KIND_BINARY = "binary"

#: PNG magic - shared with the raster helper, kept here so sniffing needs no
#: import of the codec.
_PNG_SIG = b"\x89PNG\r\n\x1a\n"

_WINDOWS_FLAGS = 0
if os.name == "nt":  # pragma: no cover - Windows only
    _WINDOWS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


# ---------------------------------------------------------------------------
# Optional external tools
# ---------------------------------------------------------------------------

def _which(name: str, env: str) -> Optional[str]:
    """Path of an optional binary, with a MESH_* override for a bare PATH.

    A Windows install of ffmpeg often lives outside PATH (winget, a portable
    unzip). ``MESH_FFMPEG`` / ``MESH_FFPROBE`` / ``MESH_PDFTOTEXT`` name it
    explicitly without asking the operator to edit the machine PATH.
    """
    override = (os.environ.get(env) or "").strip()
    if override and os.path.isfile(override):
        return override
    found = shutil.which(name)
    return found or None


def ffmpeg_path() -> Optional[str]:
    return _which("ffmpeg", "MESH_FFMPEG")


def ffprobe_path() -> Optional[str]:
    return _which("ffprobe", "MESH_FFPROBE")


def pdftotext_path() -> Optional[str]:
    return _which("pdftotext", "MESH_PDFTOTEXT")


def pdftoppm_path() -> Optional[str]:
    return _which("pdftoppm", "MESH_PDFTOPPM")


def _have(module: str) -> bool:
    """True when an optional Python package can be imported.

    ``importlib.util.find_spec`` is not used because a broken install raises from
    it as well; a real import inside try/except is the only honest answer.
    """
    try:
        __import__(module)
        return True
    except Exception:
        return False


def available_backends() -> Dict[str, bool]:
    """Which optional accelerators this node really has.

    Reported inside every answer so a degraded file (text without page images, a
    video without frames) says *why* instead of looking like a parsing bug.
    """
    return {
        "ffmpeg": ffmpeg_path() is not None,
        "ffprobe": ffprobe_path() is not None,
        "pdftotext": pdftotext_path() is not None,
        "pdftoppm": pdftoppm_path() is not None,
        "pymupdf": _have("pymupdf") or _have("fitz"),
        "pypdf": _have("pypdf"),
    }


def _run(argv: List[str], timeout: float = COMMAND_TIMEOUT) -> Tuple[int, bytes, str]:
    """Run an optional tool, never raising: (returncode, stdout, stderr tail)."""
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=_WINDOWS_FLAGS,
        )
    except FileNotFoundError:
        return 127, b"", "%s is not installed" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, b"", "%s did not finish within %.0fs" % (os.path.basename(argv[0]), timeout)
    except Exception as exc:  # pragma: no cover - defensive
        return 1, b"", "%s failed: %s" % (os.path.basename(argv[0]), exc)
    # Windows tools speak the console code page; utf-8 with replacement keeps the
    # message readable without guessing a decoding for every tool.
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    return completed.returncode, completed.stdout or b"", stderr

def short_text(value: Any, limit: int = 220) -> str:
    """One line, bounded: tool output must not smuggle newlines into a text block.

    ffmpeg writes several lines of diagnostics per failure, and those lines carry
    control characters a JSON-RPC client may refuse. A failure is worth one
    readable sentence, not the whole log.
    """
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def sanitize_text(value: str) -> str:
    """Keep a text block printable: control bytes never reach a JSON-RPC client.

    A JSON string can carry them escaped, so this is not about validity: it is
    about the model. An ANSI-coloured log would spend context on escape codes, and
    a stray NUL or backspace makes a block look corrupt. Newline, carriage return
    and tab survive because they carry meaning; everything below 32 does not.
    """
    if not value:
        return ""
    text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", value)
    return "".join(
        char for char in text
        if char in ("\n", "\r", "\t") or (ord(char) >= 32 and ord(char) != 127)
    )


# ---------------------------------------------------------------------------
# OCR: the text layer a client that drops image blocks still receives
# ---------------------------------------------------------------------------
#
# Measured on 2026-11-24: the Gemini MCP connector answers that the image did not
# reach it for a perfectly valid image block, so the pixels never enter the model
# context. An image is then only as useful as the text that can be extracted from
# it, and the node asks whatever OCR it has for that text:
#
#   * tesseract on PATH (or MESH_TESSERACT) - the classic, multilingual;
#   * any external command named by MESH_OCR_CMD - the escape hatch for
#     Windows.Media.Ocr, a cloud vision call, or an engine the operator prefers.
#     It receives the image path as its last argument and prints text to stdout.
#
# The result is added to the TEXT block, which every client renders, so a
# screenshot still arrives as words even when its picture does not.

OCR_MAX_CHARS = 4000



def ocr_argv() -> Optional[List[str]]:
    """(command prefix) of the OCR backend, or None when the node has none."""
    custom = (os.environ.get("MESH_OCR_CMD") or "").strip()
    if custom:
        # posix=False keeps Windows backslashes intact; a path with spaces is quoted
        # by the operator exactly as in a shell.
        try:
            parts = shlex.split(custom, posix=False)
        except ValueError:
            return None
        parts = [part.strip(chr(34)) for part in parts if part.strip()]
        return parts or None
    binary = _which("tesseract", "MESH_TESSERACT")
    if binary:
        return [binary]
    # Zero-config path on Windows: the bundled helper speaks Windows.Media.Ocr, so
    # an operator who installed the one optional package gets OCR without setting
    # anything. The helper is a separate process on purpose - winsdk is imported
    # there, never in the tool module.
    helper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "tools", "ocr_windows.py")
    if os.path.isfile(helper) and _have("winsdk"):
        return [sys.executable, helper]
    return None


def ocr_languages(binary: str) -> str:
    """rus+eng when tesseract has both, else whatever it does have."""
    code, out, _err = _run([binary, "--list-langs"], timeout=COMMAND_TIMEOUT)
    if code != 0:
        return "eng"
    languages = [line.strip() for line in out.decode("utf-8", "replace").splitlines()
                 if line.strip() and not line.lower().startswith("list of available")]
    wanted = [name for name in ("rus", "eng") if name in languages]
    if wanted:
        return "+".join(wanted)
    return languages[0] if languages else "eng"



def ocr_image(path: str, max_chars: int = OCR_MAX_CHARS) -> Tuple[str, str, Optional[str]]:
    """(text, backend description, note) for one image, never raising.

    A missing backend is a note, not an error: the caller still has the image block
    and the file path, and the answer says plainly that no text could be read.
    """
    argv = ocr_argv()
    if not argv:
        return "", "", ("no OCR backend on this node: install tesseract "
                        "(winget install UB-Mannheim.TesseractOCR) or point MESH_OCR_CMD "
                        "at any command that prints the text of an image")
    command = list(argv)
    if os.path.basename(command[0]).lower().startswith("tesseract"):
        languages = ocr_languages(command[0])
        backend = "tesseract (%s)" % languages
        command += [path, "stdout", "-l", languages, "--psm", "3"]
    else:
        backend = "MESH_OCR_CMD"
        command.append(path)
    code, out, err = _run(command, timeout=COMMAND_TIMEOUT)
    if code != 0:
        return "", backend, "OCR failed: %s" % short_text(err or ("exit code %d" % code))
    # A Windows helper writes the console code page unless it opts into UTF-8, and a
    # tesseract build writes UTF-8: the same tolerant decoder the text branch uses
    # gives both a chance instead of a row of replacement characters.
    text = sanitize_text(decode_text(out)[0])
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if not text:
        return "", backend, "OCR found no text in this image"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[OCR truncated at %d characters]" % max_chars
    return text, backend, None


def work_dir(prefix: str = "run-") -> str:
    """A scratch directory for ffmpeg output, inside the node cache.

    ``tempfile.gettempdir()`` is the obvious place and the wrong one twice: a
    Windows service has a temp directory its own user may not own, and a sandboxed
    or containerised node cannot write outside its mounted volume. The project
    already resolves a writable cache (``MESH_CACHE_DIR``, then
    ``~/.cache/remote-harness-mcp``) for captures, jobs and shares, so media work
    belongs there too - and an operator can move the whole thing with one variable.
    """
    base = os.path.join(paths.cache_path("media"), "work")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = tempfile.gettempdir()
    # NOT tempfile.mkdtemp: on Windows Python creates that directory with mode
    # 0o700, which really does write a restrictive DACL - the child process ffmpeg
    # then cannot open its own output, and a second process cannot even list the
    # directory (measured: WinError 5). A plain makedirs inherits the parent, which
    # is exactly what a helper process needs.
    target = os.path.join(base, "%s%s" % (prefix, uuid.uuid4().hex[:12]))
    os.makedirs(target, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# Type detection: signature first, extension second, content third
# ---------------------------------------------------------------------------

def _sniff_signature(head: bytes) -> Optional[Tuple[str, str]]:
    """(mime, kind) from the first bytes - the only evidence a browser trusts too.

    An extension is a claim, a signature is a fact: a ``.png`` renamed to ``.jpg``
    or a ``.txt`` that is really a PDF would otherwise be routed to the wrong
    branch and returned as garbage text.
    """
    if head.startswith(_PNG_SIG):
        return "image/png", KIND_IMAGE
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg", KIND_IMAGE
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", KIND_IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", KIND_IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", KIND_AUDIO
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo", KIND_VIDEO
    if head[:5] == b"%PDF-":
        return "application/pdf", KIND_PDF
    if head[:4] == b"OggS":
        return "audio/ogg", KIND_AUDIO
    if head[:4] == b"fLaC":
        return "audio/flac", KIND_AUDIO
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"):
        return "audio/mpeg", KIND_AUDIO
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return "audio/mp4", KIND_AUDIO
        return "video/mp4", KIND_VIDEO
    if head[4:8] in (b"moov", b"mdat", b"wide", b"free"):
        return "video/quicktime", KIND_VIDEO
    if head[:4] == b"\x1aE\xdf\xa3":
        return "video/x-matroska", KIND_VIDEO
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff", KIND_IMAGE
    if head[:2] == b"BM":
        return "image/bmp", KIND_IMAGE
    if head[:4] == b"PK\x03\x04":
        return "application/zip", KIND_ARCHIVE
    return None


def _kind_for_mime(mime: str) -> str:
    """Bucket a mime type into the branch that will handle it."""
    mime = (mime or "").lower()
    if mime == "image/svg+xml":
        # XML with pictures in it: still text, and no browser-side renderer here.
        return KIND_TEXT
    if mime.startswith("image/"):
        return KIND_IMAGE
    if mime.startswith("audio/"):
        return KIND_AUDIO
    if mime.startswith("video/"):
        return KIND_VIDEO
    if mime == "application/pdf":
        return KIND_PDF
    if mime in ("application/zip", "application/x-zip-compressed"):
        return KIND_ARCHIVE
    if mime.startswith("text/") or mime in (
            "application/json", "application/xml", "application/x-yaml",
            "application/x-sh", "application/javascript", "application/x-httpd-php"):
        return KIND_TEXT
    return KIND_BINARY


_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".html", ".htm",
    ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".py", ".ps1", ".psm1", ".sh",
    ".bash", ".zsh", ".bat", ".cmd", ".sql", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".java", ".kt", ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".r", ".m", ".swift",
    ".dockerfile", ".gitignore", ".env", ".properties", ".gradle", ".make", ".mk",
}


def _is_bom_utf16(head: bytes) -> bool:
    return head[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def _looks_text(head: bytes) -> bool:
    """Cheap "is this text?" test for a binary-looking extension or no extension.

    A UTF-16 file is full of NUL bytes by definition, so its BOM is checked first;
    otherwise NUL means binary (no text format this node reads contains one).
    """
    if not head:
        return False
    if _is_bom_utf16(head) or head.startswith(codecs.BOM_UTF8) or head.startswith(codecs.BOM_UTF32_LE):
        return True
    if b"\x00" in head:
        return False
    sample = head[:2048]
    control = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 12, 13))
    return control * 20 <= len(sample)


def sniff_name(path: str, head: bytes) -> Tuple[str, str]:
    """(mime, kind) using signature, then extension, then a text heuristic."""
    signature = _sniff_signature(head)
    if signature:
        return signature
    guessed, _encoding = mimetypes.guess_type(path)
    if guessed:
        kind = _kind_for_mime(guessed)
        if kind != KIND_BINARY:
            return guessed, kind
    suffix = os.path.splitext(path)[1].lower()
    # A known text extension is trusted: a log full of ANSI colour codes and stray
    # NUL bytes is still a log, and refusing it would send the caller to a tool that
    # produces exactly the mojibake this one exists to avoid. The bytes are decoded
    # and sanitised, never echoed raw. A binary guess (a .bin, a .dat) does not stop
    # this check: an extension is a claim, the bytes are the evidence.
    if suffix in _TEXT_SUFFIXES:
        return "text/plain", KIND_TEXT
    if _looks_text(head):
        return "text/plain", KIND_TEXT
    if guessed:
        return guessed, KIND_BINARY
    return "application/octet-stream", KIND_BINARY


# ---------------------------------------------------------------------------
# Text: one encoding that is really right beats a replacement character
# ---------------------------------------------------------------------------

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _control_share(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for char in text if ord(char) < 32 and char not in "\t\n\r\f\b")
    return bad / len(text)


def _cyrillic_share(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    cyrillic = sum(1 for char in letters if "\u0400" <= char <= "\u04ff")
    return cyrillic / len(letters)


def decode_text(raw: bytes, preferred: str = "") -> Tuple[str, str, List[str]]:
    """Decode bytes to text with the encoding that actually fits: (text, encoding, notes).

    ``utf-8`` is tried first because it is the only encoding that fails loudly on
    the wrong guess. A failure then has two very different meanings on a Windows
    machine - a Russian ``cp1251`` file or a Western ``cp1252`` one - and the two
    are told apart by what the bytes look like once decoded: cp1251 turns high
    bytes into Cyrillic letters, cp1252 into accented Latin ones, while a Cyrillic
    file decoded as cp1252 gives control characters and no words.
    ``latin-1`` is last because it never fails, which is exactly why it must not
    be tried first.
    """
    notes: List[str] = []
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(encoding), encoding, notes
            except UnicodeDecodeError:
                break
    wanted = (preferred or "").strip().lower()
    if wanted:
        try:
            return raw.decode(wanted), wanted, notes
        except (LookupError, UnicodeDecodeError) as exc:
            notes.append("the caller asked for %s, which did not decode: %s" % (wanted, exc))
    try:
        return raw.decode("utf-8"), "utf-8", notes
    except UnicodeDecodeError:
        pass
    try:
        candidate = raw.decode("cp1251")
        if _cyrillic_share(candidate) >= 0.35:
            notes.append("not valid utf-8: decoded as cp1251 (Cyrillic text)")
            return candidate, "cp1251", notes
    except UnicodeDecodeError:
        pass
    try:
        candidate = raw.decode("cp1252")
        if _control_share(candidate) <= 0.01:
            notes.append("not valid utf-8: decoded as cp1252 (Windows Western)")
            return candidate, "cp1252", notes
    except UnicodeDecodeError:
        pass
    candidate = raw.decode("latin-1")
    notes.append("not valid utf-8/cp1251/cp1252: decoded as latin-1, accents may be wrong")
    return candidate, "latin-1", notes


def _human_bytes(count: int) -> str:
    if count >= 1024 * 1024:
        return "%.1f MiB" % (count / (1024.0 * 1024.0))
    if count >= 1024:
        return "%.1f KiB" % (count / 1024.0)
    return "%d bytes" % count


def paginate(text: str, offset: int, max_chars: int) -> Tuple[str, bool, Optional[int]]:
    """(chunk, truncated, next_cursor) by characters, never dropping the middle."""
    total = len(text)
    start = max(0, min(int(offset or 0), total))
    limit = max(500, int(max_chars or DEFAULT_MAX_CHARS))
    end = min(total, start + limit)
    return text[start:end], end < total, (end if end < total else None)


# ---------------------------------------------------------------------------
# MCP blocks
# ---------------------------------------------------------------------------

def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def image_block(mime: str, data: bytes) -> Dict[str, Any]:
    """An ``ImageContent`` block: base64 plus the mime type that justifies it."""
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}


def audio_block(mime: str, data: bytes) -> Dict[str, Any]:
    """An ``AudioContent`` block, byte-for-byte the same shape as an image one."""
    return {"type": "audio", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}


class _Budget(object):
    """How much base64 one response may still carry.

    Counting before appending is what keeps a six-frame video from turning into a
    response no client accepts. What did not fit is reported, never silently
    dropped.
    """

    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self.used = 0

    def fits(self, encoded_chars: int) -> bool:
        return self.used + encoded_chars <= self.limit

    def spend(self, encoded_chars: int) -> None:
        self.used += encoded_chars

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)


def b64_length(raw_bytes: int) -> int:
    """Base64 characters produced by *raw_bytes* bytes (no newlines)."""
    return 4 * ((raw_bytes + 2) // 3)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def prepare_image(target: str, data: bytes, mime: str, max_side: int,
                  max_image_bytes: int) -> Tuple[Optional[bytes], str, Optional[str], Dict[str, Any]]:
    """(bytes, mime, problem, info) for one image that must fit the budget.

    An image that already fits is sent untouched: re-encoding a PNG that is already
    small would add a generation of artefacts and a few hundred milliseconds for
    nothing. PNG is shrunk in pure Python; every other format needs ffmpeg, and
    without it the caller is told exactly that instead of receiving a file the
    model cannot read.
    """
    info: Dict[str, Any] = {"source_bytes": len(data)}
    measured = raster.image_size(data)
    block_mime = measured[0] if measured else mime
    if measured:
        info["source_pixels"] = [measured[1], measured[2]]
    over_bytes = len(data) > max_image_bytes
    over_pixels = bool(measured) and max_side > 0 and max(measured[1], measured[2]) > max_side
    # A picture that is already small enough is sent untouched - unless it is also
    # far wider than the budget wants. A vision model pays for pixels even when the
    # PNG compresses well, so a 2520 px desktop is shrunk even at 190 KB; a JPEG the
    # node cannot re-encode without ffmpeg is passed through instead of refused.
    if not over_bytes and not over_pixels:
        info["action"] = "unchanged"
        return data, block_mime, None, info
    if over_pixels and not over_bytes and block_mime != "image/png" and not ffmpeg_path():
        info["action"] = "unchanged"
        info["note"] = "wider than %d px but small enough in bytes; no re-encoder here" % max_side
        return data, block_mime, None, info
    if block_mime == "image/png":
        try:
            width, height, rows = raster.decode_png(data)
        except raster.RasterError as exc:
            return None, block_mime, "this PNG cannot be re-encoded here: %s" % exc, info
        side = min(int(max_side), max(width, height)) if max_side > 0 else max(width, height)
        encoded = b""
        sent = (width, height)
        for _attempt in range(3):
            sent_w, sent_h, small = raster.shrink_rgb(width, height, rows, side)
            encoded = raster.encode_png(sent_w, sent_h, small)
            sent = (sent_w, sent_h)
            if len(encoded) <= max_image_bytes:
                break
            if side <= 480:
                break
            side = max(480, int(side * 0.7))
        info["action"] = "downscaled"
        info["sent_pixels"] = [sent[0], sent[1]]
        info["sent_bytes"] = len(encoded)
        problem = None
        if len(encoded) > max_image_bytes:
            problem = ("the image is still %s after downscaling to %dx%d; a strict client "
                       "may reject it" % (_human_bytes(len(encoded)), sent[0], sent[1]))
        return encoded, "image/png", problem, info
    converter = ffmpeg_path()
    if not converter:
        return None, block_mime, (
            "this %s is %s, above the %s image budget, and this node has no ffmpeg to "
            "downscale it. Install ffmpeg (winget install Gyan.FFmpeg), or share_file the "
            "path, or ask the sender for a smaller copy." % (
                block_mime, _human_bytes(len(data)), _human_bytes(max_image_bytes))), info
    workdir = work_dir("image-")
    output = os.path.join(workdir, "image.jpg")
    try:
        scale = "scale='min(%d,iw)':-2:flags=area" % max(320, int(max_side))
        code, _out, err = _run([converter, "-v", "error", "-y", "-i", target,
                                "-vf", scale, "-frames:v", "1", "-q:v", "4", output])
        if code != 0 or not os.path.isfile(output) or os.path.getsize(output) == 0:
            return None, block_mime, "ffmpeg could not convert this image: %s" % (short_text(err) or "no output"), info
        with open(output, "rb") as handle:
            converted = handle.read()
        info["action"] = "converted"
        info["sent_bytes"] = len(converted)
        measured_output = raster.image_size(converted)
        if measured_output:
            info["sent_pixels"] = [measured_output[1], measured_output[2]]
        return converted, "image/jpeg", None, info
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# PDF: text first, page pictures only when the text is not there
# ---------------------------------------------------------------------------

_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page[^s]")
_PDF_COUNT_RE = re.compile(rb"/Count\s+(\d+)")
_PDF_STREAM_RE = re.compile(rb"stream\r?\n")
_PDF_TJ_ARRAY_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
_PDF_TJ_SINGLE_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|\x27|\x22)", re.S)
_PDF_STRING_RE = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)


def _optional_module(candidates: Iterable[str]):
    """First importable module from *candidates*, or None."""
    for name in candidates:
        try:
            return __import__(name)
        except Exception:
            continue
    return None


def pdf_page_count(path: str, data: bytes) -> int:
    """Number of pages, from the best source available.

    A real parser wins because it follows the page tree; the fallback counts
    ``/Type /Page`` objects in the raw bytes, which is the same number for every
    PDF written without object streams (and says 0 rather than lying when it
    cannot tell).
    """
    module = _optional_module(("pymupdf", "fitz"))
    if module is not None:
        try:
            document = module.open(path)
            try:
                return int(document.page_count)
            finally:
                document.close()
        except Exception:
            pass
    pypdf = _optional_module(("pypdf",))
    if pypdf is not None:
        try:
            return len(pypdf.PdfReader(path).pages)
        except Exception:
            pass
    counts = [int(value) for value in _PDF_COUNT_RE.findall(data)]
    if counts:
        guess = max(counts)
        if 0 < guess <= 5000:
            return guess
    return len(_PDF_PAGE_RE.findall(data))


def _pdf_unescape(raw: bytes) -> str:
    """Resolve the escapes a PDF literal string may contain."""
    out: List[str] = []
    index = 0
    total = len(raw)
    simple = {
        b"n": "\n", b"r": "\r", b"t": "\t", b"b": "\b", b"f": "\f",
        b"(": "(", b")": ")", b"\\": "\\",
    }
    while index < total:
        char = raw[index:index + 1]
        if char == b"\\" and index + 1 < total:
            following = raw[index + 1:index + 2]
            if following in simple:
                out.append(simple[following])
                index += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[index + 1:index + 4])
            if octal:
                out.append(chr(int(octal.group(0), 8)))
                index += 1 + len(octal.group(0))
                continue
            index += 2
            continue
        out.append(chr(raw[index]))
        index += 1
    return "".join(out)


def _pdf_strings(chunk: bytes) -> str:
    """Text carried by one content stream, in reading order as far as it goes.

    ``TJ`` arrays interleave strings with kerning numbers, so their pieces are
    joined without a space; separate ``Tj`` operators get one, because that is
    where a word break usually is. Layout (columns, tables) is not reconstructed -
    this is the fallback for a PDF no real extractor could open, and it says so.
    """
    pieces: List[str] = []
    for array in _PDF_TJ_ARRAY_RE.finditer(chunk):
        parts = [_pdf_unescape(match.group(0)[1:-1]) for match in _PDF_STRING_RE.finditer(array.group(1))]
        if parts:
            pieces.append("".join(parts))
    for match in _PDF_TJ_SINGLE_RE.finditer(chunk):
        literal = _PDF_STRING_RE.match(match.group(0))
        if literal:
            pieces.append(_pdf_unescape(literal.group(0)[1:-1]))
    text = " ".join(piece for piece in pieces if piece.strip())
    return re.sub(r"[ \t]+", " ", text).strip()


def _pdf_builtin_text(data: bytes) -> str:
    """Best-effort text from the raw streams of a PDF (no object tree walk)."""
    chunks: List[str] = []
    for match in _PDF_STREAM_RE.finditer(data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            break
        raw = data[start:end]
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        if b"BT" not in raw and b"Tj" not in raw and b"TJ" not in raw:
            continue
        extracted = _pdf_strings(raw)
        if extracted:
            chunks.append(extracted)
        if len("\n".join(chunks)) > 200000:
            break
    return "\n\n".join(chunks)


def pdf_text(path: str, data: bytes, page_start: int, page_end: int) -> Tuple[str, str]:
    """(text, backend) for pages ``page_start``..``page_end`` (1-based).

    Order is deliberate: a real PDF library first (page-accurate text), then the
    poppler binary (same accuracy, no Python package), then the builtin stream
    reader, which cannot tell pages apart and says so in its backend name.
    """
    module = _optional_module(("pymupdf", "fitz"))
    if module is not None:
        try:
            document = module.open(path)
            try:
                total = int(document.page_count)
                first = max(1, page_start)
                last = min(total, page_end)
                parts: List[str] = []
                for number in range(first, last + 1):
                    try:
                        page_text = document[number - 1].get_text()
                    except Exception:
                        page_text = ""
                    parts.append("--- page %d/%d ---\n%s" % (number, total, page_text.rstrip()))
                return "\n\n".join(parts), "pymupdf"
            finally:
                document.close()
        except Exception:
            pass
    pypdf = _optional_module(("pypdf",))
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(path)
            total = len(reader.pages)
            first = max(1, page_start)
            last = min(total, page_end)
            parts = []
            for number in range(first, last + 1):
                try:
                    page_text = reader.pages[number - 1].extract_text() or ""
                except Exception:
                    page_text = ""
                parts.append("--- page %d/%d ---\n%s" % (number, total, page_text.rstrip()))
            return "\n\n".join(parts), "pypdf"
        except Exception:
            pass
    binary = pdftotext_path()
    if binary:
        first = max(1, page_start)
        code, out, err = _run([binary, "-f", str(first), "-l", str(max(first, page_end)),
                               "-enc", "UTF-8", "-layout", path, "-"])
        if code == 0 and out.strip():
            return out.decode("utf-8", "replace"), "pdftotext"
    return _pdf_builtin_text(data), "builtin"


def pdf_page_images(path: str, page_start: int, page_end: int, dpi: int = 100) -> Tuple[List[bytes], str, str]:
    """(images, mime, note) for the requested pages, rendered by whatever is present.

    A scanned PDF has no text layer, so the only honest thing to send is the page
    as a picture. PyMuPDF renders in-process; poppler does the same through
    ``pdftoppm``. Without either, the note names the missing backend instead of
    returning an empty list that looks like a bug.
    """
    module = _optional_module(("pymupdf", "fitz"))
    if module is not None:
        try:
            document = module.open(path)
            try:
                total = int(document.page_count)
                images: List[bytes] = []
                for number in range(max(1, page_start), min(total, page_end) + 1):
                    pixmap = document[number - 1].get_pixmap(dpi=dpi)
                    images.append(pixmap.tobytes("png"))
                return images, "image/png", "rendered by pymupdf at %d dpi" % dpi
            finally:
                document.close()
        except Exception as exc:
            return [], "image/png", "pymupdf could not render the pages: %s" % short_text(exc)
    binary = pdftoppm_path()
    if binary:
        workdir = work_dir("pdf-")
        prefix = os.path.join(workdir, "page")
        try:
            code, _out, err = _run([binary, "-r", str(dpi), "-jpeg",
                                    "-f", str(max(1, page_start)),
                                    "-l", str(max(page_start, page_end)), path, prefix])
            if code != 0:
                return [], "image/jpeg", "pdftoppm failed: %s" % (short_text(err) or "no output")
            images = []
            for name in sorted(os.listdir(workdir)):
                if name.lower().endswith(".jpg"):
                    with open(os.path.join(workdir, name), "rb") as handle:
                        images.append(handle.read())
            return images, "image/jpeg", "rendered by pdftoppm at %d dpi" % dpi
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return [], "image/png", (
        "no PDF renderer on this node: install PyMuPDF (pip install pymupdf) or "
        "poppler (pdftoppm) to receive page images")


# ---------------------------------------------------------------------------
# Audio and video: metadata always, pictures and sound only when they fit
# ---------------------------------------------------------------------------

def probe_media(path: str) -> Optional[Dict[str, Any]]:
    """ffprobe output as a dict, or None when ffprobe is missing or fails."""
    binary = ffprobe_path()
    if not binary:
        return None
    code, out, _err = _run([binary, "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", path])
    if code != 0 or not out.strip():
        return None
    try:
        parsed = json.loads(out.decode("utf-8", "replace"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def first_stream(probe: Dict[str, Any], kind: str) -> Dict[str, Any]:
    for stream in probe.get("streams") or []:
        if str(stream.get("codec_type") or "") == kind:
            return stream
    return {}


def as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wav_info(path: str) -> Dict[str, Any]:
    """Header facts of a WAV file, read with the standard library."""
    info: Dict[str, Any] = {}
    try:
        with wave.open(path, "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 0
            info["sample_rate"] = rate
            info["channels"] = handle.getnchannels()
            info["sample_width"] = handle.getsampwidth()
            info["duration"] = round(frames / float(rate), 2) if rate else None
            info["codec"] = "pcm"
    except Exception:
        return {}
    return info


def mp3_bitrate(data: bytes) -> Optional[int]:
    """Bitrate in kbit/s from the first MPEG audio frame header (CBR assumption)."""
    offset = 0
    if data[:3] == b"ID3" and len(data) > 10:
        size_bytes = data[6:10]
        offset = 10 + ((size_bytes[0] & 0x7F) << 21 | (size_bytes[1] & 0x7F) << 14
                       | (size_bytes[2] & 0x7F) << 7 | (size_bytes[3] & 0x7F))
    while offset + 4 < len(data):
        if data[offset] != 0xFF or (data[offset + 1] & 0xE0) != 0xE0:
            offset += 1
            continue
        version = (data[offset + 1] >> 3) & 0x03
        layer = (data[offset + 1] >> 1) & 0x03
        table_index = (data[offset + 2] >> 4) & 0x0F
        if version == 1 or layer == 0 or table_index in (0, 15):
            offset += 1
            continue
        table = {
            3: (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
            2: (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
            1: (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        }.get(layer)
        if not table:
            return None
        return int(table[table_index - 1])
    return None


def video_frames(path: str, duration: Optional[float], count: int,
                 max_side: int) -> Tuple[List[bytes], str]:
    """(JPEG frames, note) sampled evenly across the clip by ffmpeg.

    Evenly spaced frames answer "what is in this clip" better than the first N
    seconds: fps=count/duration walks the whole timeline, and -frames:v stops at
    the requested number. The scale filter shrinks a 4K frame to something a
    vision model can still read.
    """
    binary = ffmpeg_path()
    if not binary:
        return [], ("no ffmpeg on this node: install it to receive frames "
                    "(winget install Gyan.FFmpeg), or share_file the clip")
    wanted = max(1, int(count or DEFAULT_MAX_FRAMES))
    workdir = work_dir("video-")
    pattern = os.path.join(workdir, "frame_%02d.jpg")
    try:
        filters = "scale='min(%d,iw)':-2:flags=area" % max(320, int(max_side))
        if duration and duration > 0:
            rate = min(1.0, max(0.01, wanted / float(duration)))
            filters = "fps=%.6f,%s" % (rate, filters)
        else:
            filters = "fps=1,%s" % filters
        code, _out, err = _run([binary, "-v", "error", "-y", "-i", path,
                                "-vf", filters, "-frames:v", str(wanted),
                                "-q:v", "4", pattern])
        frames: List[bytes] = []
        for name in sorted(os.listdir(workdir)):
            if name.lower().endswith(".jpg"):
                with open(os.path.join(workdir, name), "rb") as handle:
                    frames.append(handle.read())
        if not frames:
            return [], "ffmpeg produced no frame: %s" % (short_text(err) or "unknown error")
        if len(frames) < wanted:
            return frames, "only %d of %d frames could be extracted" % (len(frames), wanted)
        return frames, "sampled %d frames across %.1f s" % (len(frames), duration or 0.0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def audio_metadata(path: str, data: bytes, mime: str) -> Dict[str, Any]:
    """Duration, codec, channels and bitrate from the cheapest source that has them.

    The wave module knows a WAV header exactly and needs no install; an MP3 frame
    header gives a bitrate good enough for a duration estimate; everything else
    (m4a, ogg, flac) is answered by ffprobe when the node has it. A field that no
    source could provide is simply absent - a made-up duration is worse than none.
    """
    info: Dict[str, Any] = {}
    probe = probe_media(path)
    if probe:
        stream = first_stream(probe, "audio")
        container = probe.get("format") or {}
        if stream.get("codec_name"):
            info["codec"] = stream.get("codec_name")
        rate = as_int(stream.get("sample_rate"))
        if rate:
            info["sample_rate"] = rate
        channels = as_int(stream.get("channels"))
        if channels:
            info["channels"] = channels
        duration = as_float(container.get("duration") or stream.get("duration"))
        if duration:
            info["duration"] = round(duration, 2)
        bitrate = as_int(stream.get("bit_rate") or container.get("bit_rate"))
        if bitrate:
            info["bitrate"] = bitrate
    if mime == "audio/wav" or data[:4] == b"RIFF":
        header = wav_info(path)
        if header:
            info.update(header)
    if mime == "audio/mpeg" and not info.get("bitrate"):
        bitrate = mp3_bitrate(data)
        if bitrate:
            info["bitrate"] = bitrate * 1000
            if not info.get("duration"):
                info["duration"] = round(len(data) * 8.0 / (bitrate * 1000), 2)
            info.setdefault("codec", "mp3")
    return info


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def _header(target: str, name: str, mime: str, size: int, extra: Iterable[str] = ()) -> str:
    """First line of every answer: what file this is, in one glance."""
    lines = [
        "[VERIFIED BY TOOL] read_media: %s" % target,
        "name: %s | mime: %s | %s" % (name, mime, _human_bytes(size)),
    ]
    for item in extra:
        if item:
            lines.append(item)
    return "\n".join(lines)


def share_hint(target: str) -> str:
    """The one fallback that always works: publish the path the user can open."""
    return ("If this chat cannot show the file itself, share_file(path=%r) publishes it as a "
            "public link you can open in a browser.") % target


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def read_media(path: str, offset: int = 0, max_chars: int = DEFAULT_MAX_CHARS,
               max_side: int = DEFAULT_MAX_SIDE, max_frames: int = DEFAULT_MAX_FRAMES,
               include_audio: bool = False, page_start: Optional[int] = None,
               page_end: Optional[int] = None, render_pages: Optional[bool] = None,
               encoding: str = "", ocr: bool = False, max_bytes: int = DEFAULT_MAX_BYTES,
               max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
               max_total_b64: int = DEFAULT_MAX_TOTAL_B64) -> Dict[str, Any]:
    """Read one file and return it as MCP content blocks.

    The answer always starts with a ``text`` block that describes the file (name,
    mime, size, and what was done to it), followed by the blocks that carry the
    content itself. Text, JSON and logs become one ``text`` block with a cursor;
    an image becomes an ``image`` block (downscaled when it is too big); a PDF
    becomes its text, plus page pictures when there is no text layer; audio becomes
    metadata plus an ``audio`` block only when explicitly asked for; video becomes
    metadata plus a handful of evenly spaced JPEG frames.

    Every option exists to keep one answer inside what a model can accept:
    ``max_chars`` for text, ``max_side``/``max_image_bytes`` for pictures,
    ``max_frames`` for clips and scanned PDFs, ``max_total_b64`` for the whole
    response, ``include_audio`` because a client may not take audio at all.

    Returns ``{"ok": True, "content": [...]}`` or ``{"error": "..."}`` - the same
    contract as every other tool in this module.
    """
    image_budget = max(64 * 1024, int(max_image_bytes or DEFAULT_MAX_IMAGE_BYTES))
    budget = _Budget(max(0, int(max_total_b64 or DEFAULT_MAX_TOTAL_B64)))
    wanted_side = max(320, int(max_side or DEFAULT_MAX_SIDE))
    wanted_frames = max(1, min(12, int(max_frames or DEFAULT_MAX_FRAMES)))
    ocr_enabled = _as_bool(ocr, True)

    raw_path = "" if path is None else str(path).strip()
    if not raw_path:
        return {"error": "path is required"}
    target = os.path.realpath(os.path.expanduser(raw_path))
    if not os.path.exists(target):
        return {"error": "file not found: %s (nothing to read)" % target}
    if os.path.isdir(target):
        return {"error": "%s is a directory: use list_dir (or glob_find) instead" % target}
    try:
        size = os.path.getsize(target)
    except OSError as exc:
        return {"error": "cannot stat %s: %s" % (target, exc)}
    if size > max_bytes:
        return {"error": ("%s is above the %s cap for read_media. Do not retry a larger read: "
                          "share_file(path=%r) publishes it as a public link instead."
                          % (_human_bytes(size), _human_bytes(max_bytes), target))}
    if size == 0:
        return {"error": "%s is empty (0 bytes): there is nothing to send" % target}
    try:
        with open(target, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return {"error": "cannot read %s: %s" % (target, exc)}

    name = os.path.basename(target)
    mime, kind = sniff_name(target, data[:4096])
    warnings: List[str] = []
    meta: Dict[str, Any] = {"mime": mime, "kind": kind, "bytes": size}
    blocks: List[Dict[str, Any]] = []
    truncated = False
    next_cursor: Optional[int] = None
    is_text = kind == KIND_TEXT or (kind == KIND_BINARY and _looks_text(data[:4096]))
    if is_text:
        text, used_encoding, notes = decode_text(data, encoding)
        text = sanitize_text(text)
        warnings.extend(notes)
        meta["encoding"] = used_encoding
        meta["characters"] = len(text)
        meta["lines"] = text.count("\n") + 1
        chunk, truncated, next_cursor = paginate(text, offset, max_chars)
        extra = ["text: %d characters, %d lines, encoding %s"
                 % (len(text), meta["lines"], used_encoding)]
        extra.extend(notes)
        if truncated and next_cursor is not None:
            extra.append("showing characters %d-%d of %d: call read_media again with "
                         "cursor=%d for the next page" % (offset, next_cursor, len(text), next_cursor))
        header = _header(target, name, mime, size, extra)
        blocks = [text_block(header + "\n" + ("-" * 60) + "\n" + chunk)]
    elif kind == KIND_IMAGE:
        prepared, block_mime, problem, info = prepare_image(
            target, data, mime, wanted_side, image_budget)
        meta["image"] = info
        if problem:
            warnings.append(problem)
        # OCR runs on the SOURCE file, not on the downscaled copy: recognition is
        # better with more pixels, and this text is what a client that drops image
        # blocks will actually read.
        ocr_lines: List[str] = []
        if ocr_enabled:
            ocr_text, ocr_backend, ocr_note = ocr_image(target)
            if ocr_text:
                meta["ocr"] = {"backend": ocr_backend, "characters": len(ocr_text)}
                ocr_lines.append("ocr (%s):" % ocr_backend)
                ocr_lines.append(ocr_text)
            elif ocr_note:
                warnings.append(ocr_note)
                ocr_lines.append(ocr_note)
        if prepared is None:
            meta["sent"] = False
            blocks = [text_block(_header(target, name, mime, size,
                                            [problem or "unsupported image"] + ocr_lines)
                                 + "\n" + share_hint(target))]
        else:
            encoded = b64_length(len(prepared))
            if not budget.fits(encoded):
                meta["sent"] = False
                blocks = [text_block(_header(target, name, mime, size, [
                    "the encoded image needs %d base64 characters, more than this response may "
                    "carry (%d): ask for max_side=%d to receive a smaller copy"
                    % (encoded, budget.left, max(320, wanted_side // 2))]))]
                warnings.append("image does not fit the response budget")
            else:
                budget.spend(encoded)
                meta["sent"] = True
                meta["sent_mime"] = block_mime
                extra = list(ocr_lines)
                if info.get("source_pixels") and info.get("sent_pixels"):
                    extra.append("image: %dx%d sent as %dx%d %s (%s base64)" % (
                        info["source_pixels"][0], info["source_pixels"][1],
                        info["sent_pixels"][0], info["sent_pixels"][1],
                        block_mime, _human_bytes(encoded)))
                else:
                    extra.append("image: %s, %s base64" % (block_mime, _human_bytes(encoded)))
                if problem:
                    extra.append(problem)
                # The guard line exists because a client that loses the pixels leaves
                # the model with the description only, and a model that does not admit
                # that will describe a screen it never received (measured: Gemini
                # described a Telegram window while the capture showed the Gemini app).
                extra.append("the picture is the FIRST content block of this answer: if "
                             "no image reached you, say so instead of describing the "
                             "screen from memory, and offer share_file(path=...) as the link")
                header_block = text_block(_header(target, name, mime, size, extra))
                # Image FIRST on purpose. Some MCP clients forward only the first
                # content block to the model, so a text-first answer reached the model
                # as a description with no pixels (measured with Gemini 24.11.2026).
                # A client that reads every block sees the same two; a text-only client
                # still gets the words, just second in the array.
                blocks = [image_block(block_mime, prepared), header_block]
    elif kind == KIND_PDF:
        pages = pdf_page_count(target, data)
        first = max(1, int(page_start or 1))
        last = max(first, int(page_end)) if page_end is not None else first + 7
        if pages:
            # page_start beyond the end must not produce an inverted range in the header
            first = min(first, pages)
            last = max(first, min(last, pages))
        extracted, backend = pdf_text(target, data, first, last)
        extracted = sanitize_text(extracted)
        chunk, truncated, next_cursor = paginate(extracted, offset, max_chars)
        meta["pages"] = pages
        meta["text_backend"] = backend
        meta["page_start"] = first
        meta["page_end"] = last
        extra = ["pdf: %d page(s), text by %s, pages %d-%d"
                 % (pages or 0, backend, first, last)]
        if pages and last < pages:
            extra.append("more pages exist: call read_media again with page_start=%d" % (last + 1))
        if truncated and next_cursor is not None:
            extra.append("text cut at character %d of %d: continue with cursor=%d"
                         % (next_cursor, len(extracted), next_cursor))
        if backend == "builtin":
            extra.append("no PDF library on this node: text was scraped from the raw streams, "
                         "so layout and page breaks are approximate (pip install pymupdf for exact text)")
        # A scanned PDF has no text layer: page pictures are then the only useful
        # answer, and they are sent automatically instead of reporting "no text".
        wants_pictures = _as_bool(render_pages) if render_pages is not None else (len(extracted.strip()) < 200)
        page_blocks: List[Dict[str, Any]] = []
        if wants_pictures and pages:
            images, image_mime, note = pdf_page_images(
                target, first, min(last, first + wanted_frames - 1))
            # A scanned page is picture-only, and a client that drops image blocks
            # would receive nothing at all: OCR each rendered page into the text block
            # so the words arrive even when the picture does not.
            if ocr_enabled and images:
                scratch = work_dir("pdf-ocr-")
                try:
                    suffix = ".jpg" if image_mime == "image/jpeg" else ".png"
                    for index, image in enumerate(images, start=first):
                        page_file = os.path.join(scratch, "page-%04d%s" % (index, suffix))
                        try:
                            with open(page_file, "wb") as handle:
                                handle.write(image)
                        except OSError as exc:
                            warnings.append("could not stage a page for OCR: %s" % exc)
                            continue
                        page_text, page_backend, page_note = ocr_image(page_file)
                        if page_text:
                            extra.append("--- page %d (ocr, %s) ---\n%s" % (index, page_backend, page_text))
                        elif page_note:
                            warnings.append(page_note)
                finally:
                    shutil.rmtree(scratch, ignore_errors=True)
            if images:
                extra.append("page images: %s" % note)
            elif note:
                warnings.append(note)
                extra.append(note)
            for image in images:
                encoded = b64_length(len(image))
                if not budget.fits(encoded):
                    warnings.append("a page image was left out: the response budget is full")
                    extra.append("one or more page images were left out: the response budget is full")
                    break
                budget.spend(encoded)
                page_blocks.append(image_block(image_mime, image))
        meta["page_images"] = len(page_blocks)
        body = chunk if chunk.strip() else "(no text layer on these pages - see the page images below)"
        blocks = [text_block(_header(target, name, mime, size, extra) + "\n" + ("-" * 60) + "\n" + body)]
        blocks.extend(page_blocks)
    elif kind == KIND_AUDIO:
        info = audio_metadata(target, data, mime)
        meta["audio"] = info
        extra = []
        if info.get("duration") is not None:
            extra.append("duration: %ss" % info.get("duration"))
        if info.get("codec"):
            extra.append("codec: %s" % info.get("codec"))
        if info.get("sample_rate"):
            extra.append("sample rate: %s Hz" % info.get("sample_rate"))
        if info.get("channels"):
            extra.append("channels: %s" % info.get("channels"))
        if info.get("bitrate"):
            extra.append("bitrate: %s kbit/s" % int(int(info.get("bitrate")) / 1000))
        extra.append("speech is not transcribed on this node: add a speech-to-text step "
                     "(faster-whisper) on the host, or ask the sender for a transcript")
        if include_audio:
            if len(data) > DEFAULT_MAX_AUDIO_BYTES:
                extra.append("audio block not sent: %s is above the %s audio budget"
                             % (_human_bytes(len(data)), _human_bytes(DEFAULT_MAX_AUDIO_BYTES)))
            elif not budget.fits(b64_length(len(data))):
                extra.append("audio block not sent: it does not fit the response budget")
            else:
                budget.spend(b64_length(len(data)))
                meta["audio_block"] = True
        blocks = [text_block(_header(target, name, mime, size, extra))]
        if meta.get("audio_block"):
            blocks.append(audio_block(mime, data))
    elif kind == KIND_VIDEO:
        probe = probe_media(target) or {}
        stream = first_stream(probe, "video")
        container = probe.get("format") or {}
        duration = as_float(container.get("duration") or stream.get("duration"))
        video_meta: Dict[str, Any] = {"duration": duration}
        for key, source in (("codec", stream.get("codec_name")),
                            ("width", as_int(stream.get("width"))),
                            ("height", as_int(stream.get("height")))):
            if source not in (None, ""):
                video_meta[key] = source
        meta["video"] = video_meta
        extra = []
        if duration:
            extra.append("video: %.1fs" % duration)
        if video_meta.get("codec"):
            extra.append("codec: %s" % video_meta.get("codec"))
        if video_meta.get("width") and video_meta.get("height"):
            extra.append("resolution: %sx%s" % (video_meta.get("width"), video_meta.get("height")))
        frames, note = video_frames(target, duration, wanted_frames, wanted_side)
        extra.append("frames: %s" % note)
        frame_blocks: List[Dict[str, Any]] = []
        for frame in frames:
            encoded = b64_length(len(frame))
            if not budget.fits(encoded):
                extra.append("one or more frames were left out: the response budget is full")
                break
            budget.spend(encoded)
            frame_blocks.append(image_block("image/jpeg", frame))
        meta["frames"] = len(frame_blocks)
        blocks = [text_block(_header(target, name, mime, size, extra))]
        blocks.extend(frame_blocks)
    elif kind == KIND_ARCHIVE:
        from core import doc_read as _doc_read
        if _doc_read.is_office_doc(target):
            doc_text, doc_meta, doc_err = _doc_read.extract_office_text(data, name)
            if doc_err is None and doc_text is not None:
                doc_text = sanitize_text(doc_text)
                meta["office"] = doc_meta
                meta["characters"] = len(doc_text)
                chunk, truncated, next_cursor = paginate(doc_text, offset, max_chars)
                extra = ["office (%s): %d characters" % (doc_meta.get("format", "doc"), len(doc_text))]
                if truncated and next_cursor is not None:
                    extra.append("showing characters %d-%d of %d: call read_media again with "
                                 "cursor=%d for the next page" % (offset, next_cursor, len(doc_text), next_cursor))
                header = _header(target, name, mime, size, extra)
                blocks = [text_block(header + "\n" + ("-" * 60) + "\n" + chunk)]
            else:
                extra = ["office document could not be parsed (%s)" % (doc_err or "unknown error")]
                blocks = [text_block(_header(target, name, mime, size, extra) + "\n" + share_hint(target))]
        else:
            extra = ["archive: a zip container (an Office document or a plain archive). read_media "
                     "does not unpack archives; unzip it with bash_exec inside the workspace, or "
                     "convert the document to PDF first and read that"]
            blocks = [text_block(_header(target, name, mime, size, extra) + "\n" + share_hint(target))]
    else:
        extra = ["binary: this file has no text layer and no block type this tool can build",
                 "first bytes: %s" % data[:16].hex(" ")]
        blocks = [text_block(_header(target, name, mime, size, extra) + "\n" + share_hint(target))]

    if not blocks:  # pragma: no cover - every branch above appends at least one
        blocks = [text_block(_header(target, name, mime, size, ["nothing could be extracted from this file"]))]
    return {
        "ok": True,
        "path": target,
        "name": name,
        "mime": mime,
        "kind": kind,
        "bytes": size,
        "content": blocks,
        "meta": meta,
        "warnings": warnings,
        "truncated": truncated,
        "next_cursor": next_cursor,
        "backends": available_backends(),
        "budget": {"limit": budget.limit, "used": budget.used},
    }
