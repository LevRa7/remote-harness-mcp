"""Unit tests for core/fs_search.py and upgraded glob_find/grep_search."""

from __future__ import annotations

import os
import time

import pytest

from core import fs_search, mcp_tools


def test_compile_rel_glob_matches_expected_paths():
    rx, depth = fs_search.compile_rel_glob("**/*.py")
    assert depth is None
    assert rx.match("a.py")
    assert rx.match("sub/b.py")
    assert rx.match("a/b/c.py")
    assert not rx.match("a.txt")

    rx2, depth2 = fs_search.compile_rel_glob("sub/*.py")
    assert depth2 == 1
    assert rx2.match("sub/b.py")
    assert not rx2.match("b.py")
    assert not rx2.match("sub/nested/b.py")

    rx3, depth3 = fs_search.compile_rel_glob("*/b.py")
    assert depth3 == 1
    assert rx3.match("sub/b.py")
    assert not rx3.match("b.py")
    assert not rx3.match("a/b/b.py")


def test_glob_find_prunes_heavy_dirs_early(tmp_path):
    (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
    for skipped in ("node_modules", ".git", ".cache", ".bun", "$RECYCLE.BIN"):
        d = tmp_path / skipped
        d.mkdir()
        (d / "hidden.txt").write_text("skip", encoding="utf-8")

    res = fs_search.glob_find(str(tmp_path), "**/*.txt")
    assert res["count"] == 1
    assert res["files"] == [os.path.realpath(str(tmp_path / "good.txt"))]
    assert res["pruned_dirs"] >= 5
    assert res["partial"] is False

    res_all = fs_search.glob_find(str(tmp_path), "**/*.txt", include_ignored=True)
    assert res_all["count"] == 6


def test_glob_find_respects_max_depth_and_max_results(tmp_path):
    (tmp_path / "l0.txt").write_text("0", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "l1.txt").write_text("1", encoding="utf-8")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "l2.txt").write_text("2", encoding="utf-8")

    d0 = fs_search.glob_find(str(tmp_path), "**/*.txt", max_depth=0)
    assert [os.path.basename(p) for p in d0["files"]] == ["l0.txt"]

    d1 = fs_search.glob_find(str(tmp_path), "**/*.txt", max_depth=1)
    assert sorted(os.path.basename(p) for p in d1["files"]) == ["l0.txt", "l1.txt"]

    capped = fs_search.glob_find(str(tmp_path), "**/*.txt", max_results=2)
    assert capped["count"] == 2
    assert capped["truncated"] is True


def test_glob_find_returns_partial_on_time_budget(tmp_path, monkeypatch):
    for i in range(5):
        sub = tmp_path / f"dir_{i}"
        sub.mkdir()
        (sub / f"f_{i}.txt").write_text("x", encoding="utf-8")

    # Simulate an immediately expiring deadline after the first check.
    calls = {"n": 0}
    real_monotonic = time.monotonic

    def fake_monotonic():
        calls["n"] += 1
        base = real_monotonic()
        return base + (100.0 if calls["n"] > 2 else 0.0)

    monkeypatch.setattr(fs_search.time, "monotonic", fake_monotonic)
    res = fs_search.glob_find(str(tmp_path), "**/*.txt", time_budget_ms=50)
    assert res["partial"] is True
    assert "next_hint" in res


def test_grep_search_skips_large_files_and_pruned_dirs(tmp_path):
    (tmp_path / "small.py").write_text("NEEDLE = 42\n", encoding="utf-8")
    (tmp_path / "big.log").write_text("NEEDLE\n" * 1000, encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "dep.py").write_text("NEEDLE = 99\n", encoding="utf-8")

    res = fs_search.grep_search(str(tmp_path), "NEEDLE", max_file_bytes=200)
    assert res["count"] == 1
    assert res["matches"][0]["path"].endswith("small.py")
    assert res["skipped_large"] == 1
    assert res["pruned_dirs"] >= 1


def test_mcp_tools_glob_and_grep_pass_new_options(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    (tmp_path / "top.py").write_text("HELLO\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("HELLO\n", encoding="utf-8")

    g = mcp_tools.call_tool("glob_find", {"pattern": "**/*.py", "max_depth": 0, "time_budget_ms": 5000})
    assert g["count"] == 1
    assert g["files"][0].endswith("top.py")

    s = mcp_tools.call_tool("grep_search", {"pattern": "HELLO", "max_depth": 0, "time_budget_ms": 5000})
    assert s["count"] == 1
    assert s["matches"][0]["path"].endswith("top.py")
