"""Unit tests for core/platforms/windows/places.py and the win_places MCP tool."""

from __future__ import annotations

import os
import struct
import time

from core import mcp_tools
from core.platforms.windows import places


def _make_lnk_bytes(
    target_path: str,
    *,
    use_unicode: bool = False,
    with_id_list: bool = True,
) -> bytes:
    """Build a minimal valid MS-SHLLINK (.lnk) binary payload for testing."""
    flags = 0x02  # HasLinkInfo
    if with_id_list:
        flags |= 0x01  # HasLinkTargetIDList

    header = bytearray(0x4C)
    struct.pack_into("<I", header, 0, 0x4C)
    header[4:20] = bytes.fromhex("0114020000000000c000000000000046")
    struct.pack_into("<I", header, 20, flags)

    id_list = b""
    if with_id_list:
        payload = b"\x04\x00\xab\xcd\x00\x00"
        id_list = struct.pack("<H", len(payload)) + payload

    # LinkInfo header (28 bytes ANSI or 36 bytes with Unicode offsets)
    vol_id = struct.pack("<IIII", 16, 3, 0, 16)  # minimal VolumeID (16 bytes)
    ansi_bytes = target_path.encode("ascii", errors="replace") + b"\x00"
    common_suffix = b"\x00"

    if use_unicode:
        li_header_size = 0x24
        vol_off = li_header_size
        local_base_off = vol_off + len(vol_id)
        common_suffix_off = local_base_off + len(ansi_bytes)
        uni_base_off = common_suffix_off + len(common_suffix)
        uni_bytes = target_path.encode("utf-16-le") + b"\x00\x00"
        uni_suffix_off = uni_base_off + len(uni_bytes)
        uni_suffix = b"\x00\x00"
        body = vol_id + ansi_bytes + common_suffix + uni_bytes + uni_suffix
        li_size = li_header_size + len(body)
        li_header = struct.pack(
            "<IIIIIIIII",
            li_size,
            li_header_size,
            0x01,  # VolumeIDAndLocalBasePath
            vol_off,
            local_base_off,
            0,  # CommonNetworkRelativeLinkOffset
            common_suffix_off,
            uni_base_off,
            uni_suffix_off,
        )
    else:
        li_header_size = 0x1C
        vol_off = li_header_size
        local_base_off = vol_off + len(vol_id)
        common_suffix_off = local_base_off + len(ansi_bytes)
        body = vol_id + ansi_bytes + common_suffix
        li_size = li_header_size + len(body)
        li_header = struct.pack(
            "<IIIIIII",
            li_size,
            li_header_size,
            0x01,
            vol_off,
            local_base_off,
            0,
            common_suffix_off,
        )

    return bytes(header) + id_list + li_header + body


def test_parse_lnk_ansi_and_unicode_and_malformed(tmp_path):
    ansi_lnk = tmp_path / "ansi.lnk"
    ansi_lnk.write_bytes(_make_lnk_bytes(r"D:\MyProjects\resume.pdf", use_unicode=False))
    assert places.parse_lnk(str(ansi_lnk)) == r"D:\MyProjects\resume.pdf"

    uni_target = r"D:\Документы\Резюме_2026.pdf"
    uni_lnk = tmp_path / "uni.lnk"
    uni_lnk.write_bytes(_make_lnk_bytes(uni_target, use_unicode=True, with_id_list=False))
    assert places.parse_lnk(str(uni_lnk)) == uni_target

    bad_lnk = tmp_path / "broken.lnk"
    bad_lnk.write_bytes(b"not a shell link")
    assert places.parse_lnk(str(bad_lnk)) is None


def test_recent_files_orders_newest_and_filters_missing(tmp_path):
    real_a = tmp_path / "doc_a.txt"
    real_b = tmp_path / "doc_b.txt"
    real_a.write_text("a", encoding="utf-8")
    real_b.write_text("b", encoding="utf-8")

    recent_dir = tmp_path / "Recent"
    recent_dir.mkdir()

    lnk_a = recent_dir / "doc_a.lnk"
    lnk_b = recent_dir / "doc_b.lnk"
    lnk_missing = recent_dir / "gone.lnk"
    lnk_dup = recent_dir / "doc_b_copy.lnk"

    lnk_a.write_bytes(_make_lnk_bytes(str(real_a), use_unicode=True))
    lnk_b.write_bytes(_make_lnk_bytes(str(real_b), use_unicode=True))
    lnk_dup.write_bytes(_make_lnk_bytes(str(real_b), use_unicode=True))
    lnk_missing.write_bytes(_make_lnk_bytes(str(tmp_path / "does_not_exist.txt"), use_unicode=True))

    now = time.time()
    os.utime(lnk_a, (now - 100, now - 100))
    os.utime(lnk_b, (now - 10, now - 10))
    os.utime(lnk_dup, (now - 20, now - 20))
    os.utime(lnk_missing, (now, now))

    existing = places.recent_files(limit=10, recent_dir=str(recent_dir), only_existing=True)
    assert [item["name"] for item in existing] == ["doc_b.txt", "doc_a.txt"]

    all_items = places.recent_files(limit=10, recent_dir=str(recent_dir), only_existing=False)
    assert len(all_items) == 3
    assert all_items[0]["exists"] is False


def test_win_places_tool_and_batch_read_and_read_only():
    mcp_tools.configure(read_only=True)
    try:
        res = mcp_tools.call_tool("win_places", {"recent_limit": 5, "top_level_limit": 5})
        assert "error" not in res
        assert isinstance(res["known_folders"], dict)
        assert isinstance(res["recent"], list)
        assert isinstance(res["drives"], list)
        assert res["elapsed_ms"] < 5000

        batched = mcp_tools.call_tool("batch_read", {
            "ops": [{"id": "wp", "tool": "win_places", "args": {"recent_limit": 3, "top_level_limit": 3}}]
        })
        assert batched["completed"] == 1
        assert batched["results"][0]["ok"] is True
        assert "known_folders" in batched["results"][0]["data"]
    finally:
        mcp_tools.configure(read_only=False)
