"""read_media: a file becomes the MCP content block its type deserves.

The tool exists because a model cannot look at raw bytes or at a path. Every test
here pins one decision of ``core/media.py``: which block a file becomes, how much
of it fits, and what the answer says when something does not fit - because those
decisions are the difference between "the model can see the screenshot" and "the
client received a wall of base64 it silently dropped".

The fixtures are built in the test instead of being committed: a PNG is written by
the project itself, a WAV by the standard library, and the PDF is a five-object
document with an uncompressed content stream, so the builtin fallback is exercised
even on a machine with no PDF library at all.
"""

import asyncio
import base64
import json
import struct
import wave
from urllib.parse import urlparse

from starlette.requests import Request

import gateway
from core import media, mcp_tools, policy, server
from core.platforms import png as png_encoder
from core.platforms import raster


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def write_png(path, width=120, height=80, noise=False):
    """A real PNG, written by the encoder the node already ships."""
    rows = []
    for y in range(height):
        row = bytearray(width * 3)
        for x in range(width):
            row[x * 3] = (x * 255) // max(1, width)
            row[(x * 3) + 1] = (y * 255) // max(1, height)
            row[(x * 3) + 2] = ((x * y) % 256) if noise else 200
        rows.append(bytes(row))
    path.write_bytes(png_encoder.encode_rgb(width, height, rows))
    return path


def write_wav(path, seconds=1.0, rate=8000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", ((i % 40) * 100) - 2000)
                                    for i in range(int(rate * seconds))))
    return path


def write_pdf(path, text="Hello from a PDF"):
    """A minimal PDF whose text sits in an uncompressed stream.

    Real-world PDFs compress and subset fonts, but this shape is what the builtin
    fallback must read on a node with no PDF library: the point of the fixture is
    the fallback, not the library.
    """
    content = ("BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % index + payload + b"\nendobj\n"
    xref_at = len(body)
    body += b"xref\n0 %d\n" % (len(objects) + 1)
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += b"%010d 00000 n \n" % offset
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at)
    path.write_bytes(bytes(body))
    return path


def kinds(result):
    return [block["type"] for block in result["content"]]


# ---------------------------------------------------------------------------
# Text: the encoding decides whether the answer is readable at all
# ---------------------------------------------------------------------------

def test_a_cp1251_file_is_decoded_instead_of_mangled(tmp_path):
    """A Russian log written by a Windows tool is cp1251, not utf-8 with errors.

    ``read_file`` would open it with ``errors="replace"``: that is fine for source
    code and turns a legacy log into a wall of replacement characters, which is
    exactly the case a model cannot recover from.
    """
    path = tmp_path / "report.txt"
    path.write_bytes(("Технический отчёт\nВторая строка\n" * 20).encode("cp1251"))

    result = media.read_media(str(path))

    assert result["meta"]["encoding"] == "cp1251"
    assert kinds(result) == ["text"]
    assert "Технический отчёт" in result["content"][0]["text"]
    assert "\ufffd" not in result["content"][0]["text"]


def test_a_utf16_file_with_a_bom_is_read_by_its_bom(tmp_path):
    """UTF-16 is full of NUL bytes; the BOM has to win over the binary heuristic."""
    path = tmp_path / "notes.txt"
    path.write_bytes("Отчёт: всё работает\n".encode("utf-16"))

    result = media.read_media(str(path))

    assert result["kind"] == media.KIND_TEXT
    assert result["meta"]["encoding"] == "utf-16"
    assert "Отчёт: всё работает" in result["content"][0]["text"]


def test_text_is_paged_by_cursor_and_the_rest_is_named(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("".join("line %04d\n" % index for index in range(2000)), encoding="utf-8")

    first = media.read_media(str(path), max_chars=500)
    assert first["truncated"] is True
    cursor = first["next_cursor"]
    assert isinstance(cursor, int) and cursor >= 500
    assert "cursor=%d" % cursor in first["content"][0]["text"]

    second = media.read_media(str(path), max_chars=500, offset=cursor)
    assert second["truncated"] is True
    first_body = first["content"][0]["text"].split("-" * 60, 1)[1]
    second_body = second["content"][0]["text"].split("-" * 60, 1)[1]
    assert first_body != second_body
    assert "line " in second_body


def test_control_characters_never_reach_the_text_block(tmp_path):
    path = tmp_path / "coloured.log"
    path.write_bytes(b"\x1b[31mred line\x1b[0m\x00 after NUL\n")

    text = media.read_media(str(path))["content"][0]["text"]

    assert "\x1b" not in text
    assert "\x00" not in text
    assert "red line" in text


# ---------------------------------------------------------------------------
# Images: an image block, and a budget that is actually enforced
# ---------------------------------------------------------------------------

def test_a_png_becomes_an_image_block_with_its_own_mime(tmp_path):
    path = write_png(tmp_path / "chart.png")

    result = media.read_media(str(path))

    # Image first: a client that forwards only the first block still gets pixels.
    assert kinds(result) == ["image", "text"]
    block = result["content"][0]
    assert block["mimeType"] == "image/png"
    assert base64.b64decode(block["data"]).startswith(b"\x89PNG\r\n\x1a\n")
    assert path.name in result["content"][1]["text"]


def test_a_wide_screenshot_is_downscaled_to_the_side_budget(tmp_path):
    path = write_png(tmp_path / "desktop.png", width=1600, height=1000)

    result = media.read_media(str(path), max_side=800)

    info = result["meta"]["image"]
    assert info["action"] == "downscaled"
    assert max(info["sent_pixels"]) <= 800
    decoded = base64.b64decode(result["content"][0]["data"])
    assert raster.png_size(decoded) == tuple(info["sent_pixels"])


def test_an_undecodable_png_is_reported_instead_of_guessed(tmp_path):
    """A truncated PNG cannot be re-encoded, and a guess would be garbage pixels."""
    path = tmp_path / "broken.png"
    path.write_bytes(png_encoder.encode_rgb(10, 10, [bytes(30)] * 10)[:40])

    prepared, block_mime, problem, info = media.prepare_image(
        str(path), path.read_bytes(), "image/png", 1280, 10)

    assert prepared is None
    assert block_mime == "image/png"
    assert "cannot be re-encoded" in problem


def test_the_response_budget_refuses_a_block_it_cannot_carry(tmp_path):
    path = write_png(tmp_path / "chart.png", width=200, height=200, noise=True)

    result = media.read_media(str(path), max_total_b64=64)

    assert kinds(result) == ["text"]
    assert "budget" in result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# PDF, audio, video: text first, then the blocks that fit
# ---------------------------------------------------------------------------

def test_pdf_text_reaches_the_client_with_the_backend_named(tmp_path):
    path = write_pdf(tmp_path / "report.pdf")

    result = media.read_media(str(path))

    assert result["kind"] == media.KIND_PDF
    assert result["meta"]["pages"] == 1
    assert result["meta"]["text_backend"] in ("pymupdf", "pypdf", "pdftotext", "builtin")
    assert "Hello from a PDF" in result["content"][0]["text"]


def test_a_pdf_page_window_is_honoured(tmp_path):
    path = write_pdf(tmp_path / "report.pdf")

    result = media.read_media(str(path), page_start=1, page_end=1)

    assert result["meta"]["page_start"] == 1
    assert result["meta"]["page_end"] == 1


def test_a_wav_is_measured_with_the_standard_library(tmp_path):
    path = write_wav(tmp_path / "voice.wav", seconds=1.0)

    result = media.read_media(str(path))

    assert result["kind"] == media.KIND_AUDIO
    info = result["meta"]["audio"]
    assert info["sample_rate"] == 8000
    assert abs(float(info["duration"]) - 1.0) < 0.05
    assert info["channels"] == 1


def test_the_audio_block_is_opt_in(tmp_path):
    """A client that cannot take audio must not receive a megabyte of it by default."""
    path = write_wav(tmp_path / "voice.wav", seconds=0.5)

    without = media.read_media(str(path))
    with_audio = media.read_media(str(path), include_audio=True)

    assert kinds(without) == ["text"]
    assert kinds(with_audio) == ["text", "audio"]
    assert with_audio["content"][1]["mimeType"] == "audio/wav"


def test_video_without_ffmpeg_names_the_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    monkeypatch.setattr(media, "ffprobe_path", lambda: None)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))

    result = media.read_media(str(path))

    assert result["kind"] == media.KIND_VIDEO
    assert kinds(result) == ["text"]
    assert "ffmpeg" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Refusals: an error the caller can act on, never a crash
# ---------------------------------------------------------------------------

def test_a_missing_file_is_an_error_not_an_empty_answer(tmp_path):
    result = media.read_media(str(tmp_path / "absent.png"))

    assert "error" in result
    assert "not found" in result["error"]


def test_a_directory_sends_the_caller_to_list_dir(tmp_path):
    result = media.read_media(str(tmp_path))

    assert "error" in result
    assert "list_dir" in result["error"]


def test_a_file_over_the_cap_is_refused_with_the_way_out(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 4096)

    result = media.read_media(str(path), max_bytes=1024)

    assert "error" in result
    assert "share_file" in result["error"]


def test_a_binary_file_is_explained_and_never_decoded_as_text(tmp_path):
    path = tmp_path / "blob.bin"
    path.write_bytes(bytes(range(256)) * 4)

    result = media.read_media(str(path))

    assert kinds(result) == ["text"]
    assert "binary" in result["content"][0]["text"].lower()
    assert "share_file" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Wiring: the node advertises it, the server forwards it, the gateway relays it
# ---------------------------------------------------------------------------

def test_the_node_advertises_the_tool_read_only():
    spec = next(tool for tool in mcp_tools.TOOLS if tool["name"] == "read_media")

    assert spec["inputSchema"]["required"] == ["path"]
    assert spec["annotations"]["readOnlyHint"] is True
    assert spec["annotations"]["destructiveHint"] is False
    assert "read_media" in mcp_tools._READ_ONLY_ALLOWED
    assert "read_media" in mcp_tools._HANDLERS
    assert "read_media" in policy.READ_ONLY_TOOLS


def test_the_gateway_advertises_the_same_tool_and_the_same_hints():
    node = next(tool for tool in mcp_tools.TOOLS if tool["name"] == "read_media")

    assert gateway.TOOL_ANNOTATIONS["read_media"] == node["annotations"]
    assert gateway.TOOL_ANNOTATIONS["read_media"]["readOnlyHint"] is True


def test_the_server_forwards_media_blocks_and_drops_unknown_types():
    to_content = server.RemoteHarnessMCPServer._result_to_content
    media_result = {"content": [
        {"type": "text", "text": "meta"},
        {"type": "image", "data": "AAAA", "mimeType": "image/png"},
    ]}

    assert [block["type"] for block in to_content(media_result)] == ["text", "image"]

    # An unknown block type is a protocol invention: drop it, keep the rest.
    assert to_content({"content": [{"type": "video", "data": "x"},
                                    {"type": "text", "text": "kept"}]}) == [
        {"type": "text", "text": "kept"}]

    # Everything else stays one text block, exactly as before.
    plain = to_content({"stdout": "hi", "exit_code": 0})
    assert plain[0]["type"] == "text"


class _FakeRelay(object):
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def __call__(self, user, name, args):
        self.calls.append((user, name, args))
        return self.result


def _registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"node-one": {"token": "tok"}}))
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(path))


def _post(body):
    path = "/mcp?user=node-one&token=tok"
    headers = [(b"host", b"mcp.example.test"),
               (b"content-type", b"application/json"),
               (b"accept", b"application/json, text/event-stream")]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "headers": headers,
        "query_string": urlparse(path).query.encode(),
        "scheme": "https",
        "server": ("mcp.example.test", 443),
        "client": ("127.0.0.1", 12345),
    }
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def test_the_gateway_relays_the_blocks_unchanged(tmp_path, monkeypatch):
    """The client reads this answer: an image block must survive the gateway."""
    relay = _FakeRelay({"ok": True, "content": [
        {"type": "text", "text": "File: chart.png | image/png | 1.2 KiB"},
        {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
    ]})
    monkeypatch.setattr(gateway, "call_remote_tool", relay)
    _registry(tmp_path, monkeypatch)

    response = asyncio.run(gateway.messages_endpoint(_post({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "read_media", "arguments": {"path": "C:/pics/chart.png"}},
    })))
    result = json.loads(response.body)["result"]

    assert [block["type"] for block in result["content"]] == ["text", "image"]
    assert result["content"][1]["mimeType"] == "image/png"
    assert result["isError"] is False
    assert relay.calls[0][1] == "read_media"


def test_the_gateway_says_so_when_the_node_answers_without_blocks(tmp_path, monkeypatch):
    relay = _FakeRelay({"stdout": "", "exit_code": 0})
    monkeypatch.setattr(gateway, "call_remote_tool", relay)
    _registry(tmp_path, monkeypatch)

    response = asyncio.run(gateway.messages_endpoint(_post({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "read_media", "arguments": {"path": "C:/pics/chart.png"}},
    })))
    result = json.loads(response.body)["result"]

    assert result["isError"] is True
    assert "older build" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# OCR: the text layer that survives a client which drops image blocks
# ---------------------------------------------------------------------------

def fake_ocr(tmp_path, text="OCR 481516 МАЛИНА"):
    """A stand-in for any OCR command: prints fixed text and the file name."""
    helper = tmp_path / "fake_ocr.py"
    helper.write_text(
        "import sys\n"
        "print(%r)\n"
        "print(sys.argv[-1].replace(chr(92), chr(47)).split(chr(47))[-1])\n" % text,
        encoding="utf-8",
    )
    return helper


def test_ocr_text_lands_in_the_text_block(tmp_path, monkeypatch):
    """Gemini drops image blocks, so the words have to travel as text."""
    import sys
    path = write_png(tmp_path / "shot.png")
    helper = fake_ocr(tmp_path)
    monkeypatch.setenv("MESH_OCR_CMD", "%s %s" % (sys.executable, helper))

    result = media.read_media(str(path), ocr=True)

    header = result["content"][1]["text"]
    assert "ocr (MESH_OCR_CMD):" in header
    assert "OCR 481516 МАЛИНА" in header
    assert "shot.png" in header
    assert result["meta"]["ocr"]["backend"] == "MESH_OCR_CMD"


def test_without_an_ocr_backend_the_answer_says_so(tmp_path, monkeypatch):
    path = write_png(tmp_path / "shot.png")
    monkeypatch.setattr(media, "ocr_argv", lambda: None)

    result = media.read_media(str(path), ocr=True)

    assert any("no OCR backend" in note for note in result["warnings"])
    assert "no OCR backend" in result["content"][1]["text"]


def test_ocr_can_be_switched_off(tmp_path, monkeypatch):
    import sys
    path = write_png(tmp_path / "shot.png")
    helper = fake_ocr(tmp_path)
    monkeypatch.setenv("MESH_OCR_CMD", "%s %s" % (sys.executable, helper))

    result = media.read_media(str(path), ocr=False)

    assert "OCR 481516" not in result["content"][1]["text"]
