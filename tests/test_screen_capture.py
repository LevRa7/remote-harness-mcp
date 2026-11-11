"""``screen_capture``: the one MCP tool whose answer depends on the display.

The tool is advertised on every platform because a client caches ``tools/list``
per server; what differs is the answer. A Windows desktop returns a PNG, a
headless server and an Android app session return ``available: false`` with the
reason - never a black rectangle, which a model cannot tell from a real screen.
"""

import os

import pytest

from core import mcp_tools
from core.platforms import png

IS_WINDOWS = os.name == "nt"


def _spec():
    return {tool["name"]: tool for tool in mcp_tools.TOOLS}


def test_screen_capture_is_advertised_with_a_path_argument():
    spec = _spec()["screen_capture"]
    assert "path" in spec["inputSchema"]["properties"]
    # No argument is mandatory: the default destination is the node's cache.
    assert spec["inputSchema"].get("required", []) == []


def test_screen_capture_is_local_work_not_a_read_and_not_destructive():
    """Writing a PNG in the node's cache is ordinary work: no dialog, no warning."""
    hints = _spec()["screen_capture"]["annotations"]
    assert hints["readOnlyHint"] is False
    assert hints["destructiveHint"] is False
    assert hints["openWorldHint"] is False


def test_unsupported_platform_answers_with_a_reason(monkeypatch):
    class Headless(object):
        scenario = "linux"

        def screenshot_png(self):
            return None

    monkeypatch.setattr(mcp_tools.platforms, "backend", lambda scenario=None: Headless())
    result = mcp_tools.call_tool("screen_capture", {})
    assert result["available"] is False
    assert result["reason"]
    assert result["scenario"] == "linux"
    assert "path" not in result


def test_supported_platform_saves_the_png(monkeypatch, tmp_path):
    data = png.encode_rgb(2, 1, [bytes([255, 0, 0, 0, 255, 0])])

    class Desktop(object):
        scenario = "windows"

        def screenshot_png(self):
            return data

    monkeypatch.setattr(mcp_tools.platforms, "backend", lambda scenario=None: Desktop())
    target = tmp_path / "shot.png"
    result = mcp_tools.call_tool("screen_capture", {"path": str(target)})

    assert result["ok"] is True
    assert result["path"] == str(target)
    assert result["bytes"] == len(data)
    assert (result["width"], result["height"]) == (2, 1)
    assert result["mime"] == "image/png"
    assert target.read_bytes() == data


def test_default_path_is_a_timestamped_cache_file(monkeypatch, tmp_path):
    data = png.encode_rgb(1, 1, [bytes([0, 0, 0])])

    class Desktop(object):
        scenario = "windows"

        def screenshot_png(self):
            return data

    monkeypatch.setattr(mcp_tools.platforms, "backend", lambda scenario=None: Desktop())
    monkeypatch.setattr(mcp_tools.paths, "cache_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    result = mcp_tools.call_tool("screen_capture", {})
    assert result["ok"] is True
    assert os.path.basename(result["path"]).startswith("screen-")
    assert os.path.isfile(result["path"])


def test_read_only_mode_refuses_to_write_a_file(monkeypatch, tmp_path):
    data = png.encode_rgb(1, 1, [bytes([0, 0, 0])])

    class Desktop(object):
        scenario = "windows"

        def screenshot_png(self):
            return data

    monkeypatch.setattr(mcp_tools.platforms, "backend", lambda scenario=None: Desktop())
    mcp_tools.configure(read_only=True)
    try:
        result = mcp_tools.call_tool("screen_capture", {"path": str(tmp_path / "x.png")})
        assert "error" in result
        assert not (tmp_path / "x.png").exists()
    finally:
        mcp_tools.configure(read_only=False)


@pytest.mark.skipif(not IS_WINDOWS, reason="needs a real Windows desktop")
def test_real_capture_on_windows(tmp_path):
    target = tmp_path / "real.png"
    result = mcp_tools.call_tool("screen_capture", {"path": str(target)})
    assert result["ok"] is True, result
    assert result["width"] > 0 and result["height"] > 0
    assert result["bytes"] > 1024
    assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
