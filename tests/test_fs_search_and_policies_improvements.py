"""Tests for Wave 1 search & security policy improvements (Tasks 2.2, 2.3, 3.3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import fs_search, policies


# ---------------------------------------------------------------------------
# Task 2.2: format_grep_compact
# ---------------------------------------------------------------------------


def test_format_grep_compact_empty():
    assert fs_search.format_grep_compact([], "/some/root") == ""


def test_format_grep_compact_relative_forward_slashes(tmp_path):
    sub = tmp_path / "pkg" / "nested"
    sub.mkdir(parents=True)
    f1 = tmp_path / "top.py"
    f2 = sub / "mod.py"
    f1.write_text("alpha = 1\nTARGET = 2\n", encoding="utf-8")
    f2.write_text("TARGET = 3\n", encoding="utf-8")

    res = fs_search.grep_search(str(tmp_path), "TARGET", fixed=True)
    compact = fs_search.format_grep_compact(res["matches"], str(tmp_path))

    lines = compact.splitlines()
    assert len(lines) == 2
    assert "top.py:2: TARGET = 2" in lines
    assert "pkg/nested/mod.py:1: TARGET = 3" in lines
    assert "\\" not in compact


def test_format_grep_compact_when_root_is_single_file(tmp_path):
    target = tmp_path / "single.py"
    target.write_text("line1\nNEEDLE\nline3\n", encoding="utf-8")

    res = fs_search.grep_search(str(target), "NEEDLE", fixed=True)
    compact = fs_search.format_grep_compact(res["matches"], str(target))
    assert compact == "single.py:2: NEEDLE"


def test_format_grep_compact_handles_relative_and_cross_drive_paths(monkeypatch):
    matches = [
        {"path": ".\\sub\\a.py", "line": 7, "text": "hello"},
        {"path": "sub\\b.py", "line": 12, "text": "world"},
    ]
    assert fs_search.format_grep_compact(matches, "/root") == (
        "sub/a.py:7: hello\n"
        "sub/b.py:12: world"
    )

    # Simulate cross-drive ValueError from os.path.relpath on Windows.
    def _raise_value_error(*_args, **_kwargs):
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(fs_search.os.path, "relpath", _raise_value_error)
    abs_sample = os.path.abspath("other/file.py")
    out = fs_search.format_grep_compact(
        [{"path": abs_sample, "line": 3, "text": "x"}],
        os.path.abspath("."),
    )
    assert out == f"{abs_sample.replace(chr(92), '/')}:3: x"


# ---------------------------------------------------------------------------
# Task 2.3: literal bytes pre-filter in grep_search
# ---------------------------------------------------------------------------


def test_is_literal_regex_detection():
    assert fs_search._is_literal_regex("hello_world-123") is True
    assert fs_search._is_literal_regex("привет мир") is True
    assert fs_search._is_literal_regex("") is False
    for meta in (".", "^", "$", "*", "+", "?", "{1}", "[a]", "\\d", "a|b", "(a)"):
        assert fs_search._is_literal_regex(f"foo{meta}bar") is False


def test_grep_search_literal_bytes_prefilter_fixed_and_regex(tmp_path):
    (tmp_path / "hit_ascii.py").write_text("x = 1\nfoo_bar = 2\n", encoding="utf-8")
    (tmp_path / "hit_unicode.py").write_text("строка = 'привет'\n", encoding="utf-8")
    (tmp_path / "hit_meta.py").write_text("val = arr[0].sum()\n", encoding="utf-8")
    (tmp_path / "miss.py").write_text("nothing relevant here\n" * 50, encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01foo_bar\x00\x02")

    # Literal regex without metacharacters (fixed=False, ignore_case=False)
    r1 = fs_search.grep_search(str(tmp_path), "foo_bar")
    assert r1["count"] == 1
    assert r1["matches"][0]["path"].endswith("hit_ascii.py")
    assert r1["matches"][0]["line"] == 2
    # Binary file skipped; 4 text files scanned (even though 3 skipped by bytes pre-filter).
    assert r1["scanned_files"] == 4

    # Fixed search with regex metacharacters
    r2 = fs_search.grep_search(str(tmp_path), "arr[0].sum()", fixed=True)
    assert r2["count"] == 1
    assert r2["matches"][0]["path"].endswith("hit_meta.py")

    # UTF-8 non-ASCII literal search
    r3 = fs_search.grep_search(str(tmp_path), "привет", fixed=True)
    assert r3["count"] == 1
    assert r3["matches"][0]["path"].endswith("hit_unicode.py")

    # ignore_case=True bypasses case-sensitive bytes pre-filter and still matches
    r4 = fs_search.grep_search(str(tmp_path), "FOO_BAR", ignore_case=True)
    assert r4["count"] == 1
    assert r4["matches"][0]["path"].endswith("hit_ascii.py")

    # Regex with metacharacters still works properly
    r5 = fs_search.grep_search(str(tmp_path), r"foo_\w+\s*=\s*\d+")
    assert r5["count"] == 1
    assert r5["matches"][0]["path"].endswith("hit_ascii.py")


def test_grep_search_bytes_prefilter_skips_regex_on_misses(tmp_path, monkeypatch):
    (tmp_path / "miss1.txt").write_text("aaa\nbbb\n", encoding="utf-8")
    (tmp_path / "miss2.txt").write_text("ccc\nddd\n", encoding="utf-8")
    (tmp_path / "hit.txt").write_text("eee\nEXACT_TOKEN\nfff\n", encoding="utf-8")

    # Verify that literal regex path never invokes regex.search when bytes pre-filter is active.
    search_calls = []
    real_compile = fs_search.re.compile

    class _SpyPattern:
        def __init__(self, inner):
            self._inner = inner

        def search(self, text):
            search_calls.append(text)
            return self._inner.search(text)

    monkeypatch.setattr(fs_search.re, "compile", lambda pat, fl=0: _SpyPattern(real_compile(pat, fl)))

    res = fs_search.grep_search(str(tmp_path), "EXACT_TOKEN", fixed=False, ignore_case=False, context=1)
    assert res["count"] == 3
    assert [m["line"] for m in res["matches"]] == [1, 2, 3]
    assert search_calls == []


def test_grep_search_replacement_char_bypasses_bytes_prefilter(tmp_path):
    # Invalid UTF-8 byte decodes to U+FFFD under errors="replace".
    (tmp_path / "bad_utf8.txt").write_bytes(b"prefix \xff suffix\n")
    res = fs_search.grep_search(str(tmp_path), "\ufffd", fixed=True)
    assert res["count"] == 1
    assert res["matches"][0]["line"] == 1


# ---------------------------------------------------------------------------
# Task 3.3: is_protected_git_path and check_write_allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".git/config",
        ".git/hooks/pre-commit",
        ".git/hooks/nested/script.sh",
        ".git/hooks",
        "./.git/config",
        "repo/.git/config",
        "repo/.git/hooks/post-checkout",
        "repo/src/../.git/config",
        "repo/.git/./hooks/../hooks/pre-push",
        "repo\\.git\\config",
        "repo\\.git\\hooks\\pre-commit",
        "repo/.git\\hooks/pre-commit",
        "repo/.GIT/CONFIG",
        "repo/.Git/Hooks/Pre-Commit",
        "repo/.git/HOOKS/post-merge",
    ],
)
def test_is_protected_git_path_blocks_protected_variants(path):
    assert policies.is_protected_git_path(path) is True
    allowed, reason = policies.check_write_allowed(path)
    assert allowed is False
    assert reason
    assert ".git" in reason


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "README.md",
        "src/hooks/pre-commit.py",
        "src/config",
        ".gitignore",
        ".github/workflows/ci.yml",
        "my.git/config",
        "my.git/hooks/pre-commit",
        ".git",
        ".git/HEAD",
        ".git/COMMIT_EDITMSG",
        ".git/config.bak",
        ".git/hooks_backup/pre-commit",
        "repo/.git/hooks/../HEAD",
    ],
)
def test_is_protected_git_path_allows_normal_paths(path):
    assert policies.is_protected_git_path(path) is False
    allowed, reason = policies.check_write_allowed(path)
    assert allowed is True
    assert reason == ""


def test_is_protected_git_path_accepts_pathlib(tmp_path):
    git_cfg = tmp_path / ".git" / "config"
    normal = tmp_path / "app.py"
    assert policies.is_protected_git_path(git_cfg) is True
    assert policies.check_write_allowed(git_cfg)[0] is False
    assert policies.is_protected_git_path(normal) is False
    assert policies.check_write_allowed(normal) == (True, "")


def test_is_protected_git_path_windows_ads_and_trailing_dots(monkeypatch):
    monkeypatch.setattr(policies.os, "name", "nt")
    assert policies.is_protected_git_path(r"D:\repo\.git\config.") is True
    assert policies.is_protected_git_path(r"D:\repo\.git\config ") is True
    assert policies.is_protected_git_path(r"D:\repo\.git\config::$DATA") is True
    assert policies.is_protected_git_path(r"D:\repo\.GIT.\HOOKS.\pre-commit") is True
    assert policies.is_protected_git_path(r"D:\repo\src\config.py") is False


def test_is_protected_git_path_blocks_symlinks(tmp_path, monkeypatch):
    git_hooks = tmp_path / ".git" / "hooks"
    git_hooks.mkdir(parents=True)
    hook_file = git_hooks / "pre-commit"
    hook_file.write_text("#!/bin/sh\n", encoding="utf-8")
    git_config = tmp_path / ".git" / "config"
    git_config.write_text("[core]\n", encoding="utf-8")

    # 1. Always verify symlink resolution logic via realpath mock (works even
    #    on Windows without symlink creation privileges).
    fake_link = tmp_path / "innocent.txt"
    real_realpath = os.path.realpath

    def _fake_realpath(p):
        if os.path.abspath(str(p)) == os.path.abspath(str(fake_link)):
            return str(hook_file)
        return real_realpath(p)

    monkeypatch.setattr(policies.os.path, "realpath", _fake_realpath)
    assert policies.is_protected_git_path(fake_link) is True
    allowed, reason = policies.check_write_allowed(fake_link)
    assert allowed is False
    assert reason
    monkeypatch.undo()

    # 2. If the host allows creating real OS symlinks, verify end-to-end too.
    real_link = tmp_path / "link_to_config"
    dir_link = tmp_path / "link_to_hooks"
    try:
        os.symlink(str(git_config), str(real_link))
        os.symlink(str(git_hooks), str(dir_link), target_is_directory=True)
    except OSError:
        return

    assert policies.is_protected_git_path(real_link) is True
    assert policies.is_protected_git_path(dir_link / "new-hook") is True
    assert policies.check_write_allowed(dir_link / "new-hook")[0] is False
