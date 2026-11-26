"""Wave 1 MCP tool improvements tests (Tasks 1.1, 2.1, 2.2, 3.1, 3.2, 3.3)."""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import urllib.error

import pytest

from core import mcp_tools, settings

BASELINE_TOOLS_JSON_BYTES = 33517


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_TOOLSETS", raising=False)
    monkeypatch.delenv("MESH_DEVICE", raising=False)
    monkeypatch.delenv("MESH_ENABLE_SHELL", raising=False)
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        enable_shell=True,
        write_roots=None,
        device="auto",
        max_output_chars=12000,
    )
    yield tmp_path
    mcp_tools.configure(
        workspace=os.getcwd(),
        read_only=False,
        enable_shell=True,
        write_roots=None,
        device="auto",
        max_output_chars=12000,
    )


# ---------------------------------------------------------------------------
# Task 1.1: Binary file detection in read_file
# ---------------------------------------------------------------------------

def test_read_file_rejects_binary_png_and_suggests_read_media(tmp_path):
    png = tmp_path / "image.png"
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 100
    png.write_bytes(png_bytes)

    res = mcp_tools.call_tool("read_file", {"path": "image.png"})
    assert "error" in res
    assert res["binary"] is True
    assert res["size_bytes"] == len(png_bytes)
    assert res["extension"] == ".png"
    assert res["mime_type"] == "image/png"
    assert res["suggested_tool"] == "read_media"
    assert "read_media" in res["error"]


def test_read_file_detects_nul_near_8192_boundary(tmp_path):
    blob = tmp_path / "data.bin"
    blob.write_bytes(b"A" * 8000 + b"\x00" + b"B" * 500)

    res = mcp_tools.call_tool("read_file", {"path": "data.bin"})
    assert "error" in res
    assert res["binary"] is True
    assert res["size_bytes"] == 8501
    assert "read_media" in res["error"]


def test_read_file_reads_utf8_and_cp1251_text_normally(tmp_path):
    utf8_file = tmp_path / "hello.txt"
    utf8_file.write_text("Привет мир\nline two\n", encoding="utf-8")
    res = mcp_tools.call_tool("read_file", {"path": "hello.txt"})
    assert "error" not in res
    assert res["exit_code"] == 0
    assert "Привет мир" in res["stdout"]

    cp_file = tmp_path / "legacy.txt"
    cp_file.write_bytes("строка один\nстрока два\n".encode("cp1251"))
    res_cp = mcp_tools.call_tool("read_file", {"path": "legacy.txt"})
    assert "error" not in res_cp
    assert res_cp["exit_code"] == 0


# ---------------------------------------------------------------------------
# Task 2.1: tools/list compression + MESH_TOOLSETS + platform auto-profile
# ---------------------------------------------------------------------------

def test_tools_list_json_reduced_by_at_least_35_percent():
    tools_bytes = len(json.dumps(mcp_tools.TOOLS, ensure_ascii=False).encode("utf-8"))
    reduction = 1.0 - (tools_bytes / BASELINE_TOOLS_JSON_BYTES)
    assert reduction >= 0.35, (
        f"Expected >=35% reduction from {BASELINE_TOOLS_JSON_BYTES} bytes, "
        f"got {tools_bytes} bytes ({reduction * 100:.1f}%)"
    )

    auto_desktop = mcp_tools.list_tools(is_android=False, device_enabled=False)
    auto_bytes = len(json.dumps(auto_desktop, ensure_ascii=False).encode("utf-8"))
    auto_reduction = 1.0 - (auto_bytes / BASELINE_TOOLS_JSON_BYTES)
    assert auto_reduction >= 0.35, (
        f"Expected >=35% reduction for desktop auto-profile, got {auto_bytes} bytes "
        f"({auto_reduction * 100:.1f}%)"
    )


def test_mesh_toolsets_and_platform_autoprofile(monkeypatch):
    # Desktop default hides phone-only tools
    desktop = [t["name"] for t in mcp_tools.list_tools(is_android=False, device_enabled=False)]
    assert "device_messages" not in desktop
    assert "device_capture" not in desktop
    assert "device_info" in desktop
    assert "git_status" in desktop
    assert "git_diff" in desktop
    assert "fetch_url" in desktop

    # Android default includes phone-only tools
    android = [t["name"] for t in mcp_tools.list_tools(is_android=True, device_enabled=False)]
    assert "device_messages" in android
    assert "device_capture" in android

    # MESH_DEVICE=1 forces phone-only tools on desktop
    monkeypatch.setenv("MESH_DEVICE", "1")
    forced = [t["name"] for t in mcp_tools.list_tools(is_android=False)]
    assert "device_messages" in forced
    assert "device_capture" in forced
    monkeypatch.delenv("MESH_DEVICE", raising=False)

    # MESH_TOOLSETS=all includes everything
    monkeypatch.setenv("MESH_TOOLSETS", "all")
    all_names = [t["name"] for t in mcp_tools.list_tools(is_android=False, device_enabled=False)]
    assert "device_messages" in all_names
    assert "device_capture" in all_names
    assert "git_status" in all_names
    assert "fetch_url" in all_names

    # Specific subsets
    assert {t["name"] for t in mcp_tools.list_tools("git")} == {"git_status", "git_diff"}
    web_names = {t["name"] for t in mcp_tools.list_tools("web")}
    assert {"fetch_url", "share_file", "serve_dir", "share_list", "unshare"}.issubset(web_names)
    admin_names = {t["name"] for t in mcp_tools.list_tools("admin")}
    assert {"mesh_update", "node_control", "schedule"}.issubset(admin_names)
    core_git = {t["name"] for t in mcp_tools.list_tools("core,git")}
    assert "read_file" in core_git and "git_status" in core_git and "fetch_url" not in core_git

    # settings.snapshot reflects toolsets and shell_enabled
    monkeypatch.setenv("MESH_TOOLSETS", "core,git")
    monkeypatch.setenv("MESH_ENABLE_SHELL", "0")
    snap = settings.snapshot()
    assert snap["toolsets"] == "core,git"
    assert snap["shell_enabled"] is False


# ---------------------------------------------------------------------------
# Task 2.2: grep_search compact format
# ---------------------------------------------------------------------------

def test_grep_search_compact_and_json_compat(tmp_path):
    sub = tmp_path / "pkg" / "mod"
    sub.mkdir(parents=True)
    (sub / "app.py").write_text("alpha = 1\nTARGET_TOKEN = 42\nomega = 3\n", encoding="utf-8")

    # Default JSON mode stays backward compatible without duplicating compact output
    default_res = mcp_tools.call_tool("grep_search", {"pattern": "TARGET_TOKEN"})
    assert default_res["count"] == 1
    assert default_res["matches"][0]["line"] == 2
    assert "compact" not in default_res

    # Explicit compact mode returns flat rel/path:line: text without matches array
    compact_res = mcp_tools.call_tool("grep_search", {"pattern": "TARGET_TOKEN", "format": "compact"})
    assert compact_res["format"] == "compact"
    assert compact_res["count"] == 1
    assert "matches" not in compact_res
    assert compact_res["output"] == "pkg/mod/app.py:2: TARGET_TOKEN = 42"


# ---------------------------------------------------------------------------
# Task 3.1: git_status and git_diff
# ---------------------------------------------------------------------------

def test_git_status_and_git_diff_annotations_and_safe_flags(tmp_path, monkeypatch):
    ext_by_name = {t["name"]: t for t in mcp_tools.EXTENDED_TOOLS}
    for name in ("git_status", "git_diff"):
        assert ext_by_name[name]["annotations"]["readOnlyHint"] is True
        assert ext_by_name[name]["annotations"]["destructiveHint"] is False

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(argv, 0, stdout=b"## main\n", stderr=b"")

    monkeypatch.setattr(mcp_tools.subprocess, "run", fake_run)
    res = mcp_tools.call_tool("git_status", {"path": str(tmp_path)})
    assert res["exit_code"] == 0
    assert captured["shell"] is False
    assert captured["argv"][:5] == ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath="]

    # Flag injection via ref must be rejected
    bad_ref = mcp_tools.call_tool("git_diff", {"path": str(tmp_path), "ref": "--output=/tmp/evil"})
    assert "error" in bad_ref


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_status_and_git_diff_real_repo_and_read_only(tmp_path):
    def run_git(*args):
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("line1\nline2\n", encoding="utf-8")
    run_git("add", "a.txt")
    run_git("commit", "-m", "init")

    # Unstaged edit + untracked file
    (tmp_path / "a.txt").write_text("line1\nline2_changed\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("untracked\n", encoding="utf-8")

    # Works even when read_only=True
    mcp_tools.configure(read_only=True)
    st = mcp_tools.call_tool("git_status", {"path": str(tmp_path)})
    assert "error" not in st
    assert st["exit_code"] == 0
    assert "a.txt" in st["stdout"]
    assert "new.txt" in st["stdout"]

    diff = mcp_tools.call_tool("git_diff", {"path": str(tmp_path)})
    assert "error" not in diff
    assert diff["exit_code"] == 0
    assert "+line2_changed" in diff["stdout"]

    diff_stat = mcp_tools.call_tool("git_diff", {"path": str(tmp_path), "stat": True})
    assert "a.txt" in diff_stat["stdout"]

    # Truncation works
    diff_trunc = mcp_tools.call_tool("git_diff", {"path": str(tmp_path), "max_chars": 20})
    assert diff_trunc["truncated"] is True
    assert len(diff_trunc["stdout"]) == 20


# ---------------------------------------------------------------------------
# Task 3.2: fetch_url with SSRF protection
# ---------------------------------------------------------------------------

def test_fetch_url_rejects_non_https_and_ssrf_ips(monkeypatch):
    ext_by_name = {t["name"]: t for t in mcp_tools.EXTENDED_TOOLS}
    assert ext_by_name["fetch_url"]["annotations"]["readOnlyHint"] is True

    # Non-HTTPS schemes
    for bad_url in ("http://example.com", "file:///etc/passwd", "ftp://example.com/a", "https://user:pass@example.com"):
        res = mcp_tools.call_tool("fetch_url", {"url": bad_url})
        assert "error" in res, bad_url

    # Blocked IP categories
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "0.0.0.0"):
        monkeypatch.setattr(
            mcp_tools.socket, "getaddrinfo",
            lambda host, port, type=0, _ip=ip: [(socket.AF_INET6 if ":" in _ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (_ip, 443))],
        )
        res = mcp_tools.call_tool("fetch_url", {"url": "https://internal.test/secret"})
        assert "error" in res and "SSRF" in res["error"], ip


def test_fetch_url_html_stripping_size_cap_and_redirect_validation(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        if host == "public.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        if host == "evil.local":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        raise socket.gaierror("unknown host")

    monkeypatch.setattr(mcp_tools.socket, "getaddrinfo", fake_getaddrinfo)

    class _FakeResp:
        def __init__(self, body: bytes, status: int = 200, headers=None):
            self._buf = io.BytesIO(body)
            self.status = status
            self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # 1. HTML stripping + read_only allowed
    html_body = b"<html><head><style>body{color:red}</style><script>alert(1)</script></head><body><h1>Hello</h1><p>World</p></body></html>"

    class _FakeOpener:
        def open(self, req, timeout=15):
            return _FakeResp(html_body)

    monkeypatch.setattr(mcp_tools.urllib.request, "build_opener", lambda *a: _FakeOpener())
    mcp_tools.configure(read_only=True)
    res = mcp_tools.call_tool("fetch_url", {"url": "https://public.example/page"})
    assert "error" not in res
    assert res["status"] == 200
    assert "Hello" in res["content"] and "World" in res["content"]
    assert "alert(1)" not in res["content"] and "color:red" not in res["content"]

    raw_res = mcp_tools.call_tool("fetch_url", {"url": "https://public.example/page", "strip_html": False})
    assert "<h1>Hello</h1>" in raw_res["content"]

    # 2. 300 KB cap
    big_body = b"X" * (mcp_tools.FETCH_URL_MAX_BYTES + 1024)

    class _BigOpener:
        def open(self, req, timeout=15):
            return _FakeResp(big_body, headers={"Content-Type": "text/plain"})

    monkeypatch.setattr(mcp_tools.urllib.request, "build_opener", lambda *a: _BigOpener())
    big_res = mcp_tools.call_tool("fetch_url", {"url": "https://public.example/big"})
    assert big_res["truncated"] is True
    assert big_res["bytes"] == mcp_tools.FETCH_URL_MAX_BYTES

    # 3. Redirect to private IP is blocked
    class _SsrfRedirectOpener:
        def open(self, req, timeout=15):
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": "https://evil.local/admin"}, None
            )

    monkeypatch.setattr(mcp_tools.urllib.request, "build_opener", lambda *a: _SsrfRedirectOpener())
    redir_res = mcp_tools.call_tool("fetch_url", {"url": "https://public.example/redir"})
    assert "error" in redir_res and "SSRF" in redir_res["error"]

    # 4. >5 redirects is blocked
    class _LoopRedirectOpener:
        def open(self, req, timeout=15):
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": "https://public.example/loop"}, None
            )

    monkeypatch.setattr(mcp_tools.urllib.request, "build_opener", lambda *a: _LoopRedirectOpener())
    loop_res = mcp_tools.call_tool("fetch_url", {"url": "https://public.example/loop"})
    assert "error" in loop_res and "too many redirects" in loop_res["error"]


# ---------------------------------------------------------------------------
# Task 3.3: .git/hooks & .git/config protection + MESH_ENABLE_SHELL=0
# ---------------------------------------------------------------------------

def test_protected_git_paths_blocked_in_write_edit_and_batch_edit(tmp_path):
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    hook_file = hooks_dir / "pre-commit"
    hook_file.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    git_config = tmp_path / ".git" / "config"
    git_config.write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")

    # write_file blocked
    w1 = mcp_tools.call_tool("write_file", {"path": ".git/hooks/pre-commit", "content": "evil"})
    assert "error" in w1 and ".git" in w1["error"]
    w2 = mcp_tools.call_tool("write_file", {"path": ".git/config", "content": "evil"})
    assert "error" in w2 and ".git" in w2["error"]
    w3 = mcp_tools.call_tool("write_file", {"path": ".GIT/HOOKS/post-checkout", "content": "evil"})
    assert "error" in w3 and ".git" in w3["error"]

    # edit_file blocked
    e1 = mcp_tools.call_tool("edit_file", {"path": ".git/hooks/pre-commit", "old_string": "ok", "new_string": "evil"})
    assert "error" in e1 and ".git" in e1["error"]
    e2 = mcp_tools.call_tool("edit_file", {"path": ".git/config", "old_string": "0", "new_string": "1"})
    assert "error" in e2 and ".git" in e2["error"]

    # batch_edit blocked (both dry_run and real)
    for dry in (True, False):
        b = mcp_tools.call_tool("batch_edit", {
            "dry_run": dry,
            "ops": [
                {"id": "h", "op": "write", "path": ".git/hooks/pre-push", "content": "evil"},
                {"id": "c", "op": "edit", "path": ".git/config", "old_string": "0", "new_string": "1"},
            ],
            "stop_on_error": False,
        })
        assert b["failed"] == 2
        assert b["completed"] == 0

    # Regular files still writable
    ok = mcp_tools.call_tool("write_file", {"path": ".gitignore", "content": "*.pyc\n"})
    assert ok.get("ok") is True


def test_mesh_enable_shell_zero_blocks_shell_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_ENABLE_SHELL", "0")

    b = mcp_tools.call_tool("bash_exec", {"command": "echo hi"})
    assert "error" in b and "shell_disabled" in b["error"]

    s = mcp_tools.call_tool("run_script", {"code": "print(1)", "lang": "python"})
    assert "error" in s and "shell_disabled" in s["error"]

    j = mcp_tools.call_tool("run_job", {"command": "echo bg"})
    assert "error" in j and "shell_disabled" in j["error"]

    # Non-shell tools still work
    (tmp_path / "ok.txt").write_text("hello\n", encoding="utf-8")
    r = mcp_tools.call_tool("read_file", {"path": "ok.txt"})
    assert "error" not in r and "hello" in r["stdout"]
