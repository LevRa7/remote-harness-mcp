"""Unit tests for the read-only batch_read multiplexer in core/mcp_tools.py."""

from __future__ import annotations

from core import mcp_tools


def test_batch_read_executes_multiple_read_ops_in_order(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    (tmp_path / "hello.txt").write_text("line1\nline2\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("TOKEN = 1\n", encoding="utf-8")

    res = mcp_tools.call_tool("batch_read", {
        "ops": [
            {"id": "ls", "tool": "list_dir", "args": {"path": "."}},
            {"id": "rf", "tool": "read_file", "args": {"path": "hello.txt"}},
            {"id": "gf", "tool": "glob_find", "args": {"pattern": "**/*.py"}},
            {"id": "gs", "tool": "grep_search", "args": {"pattern": "TOKEN"}},
        ]
    })

    assert "error" not in res
    assert res["total"] == 4
    assert res["completed"] == 4
    assert [r["id"] for r in res["results"]] == ["ls", "rf", "gf", "gs"]
    assert all(r["ok"] is True for r in res["results"])
    assert "line1" in res["results"][1]["data"]["stdout"]
    assert res["results"][2]["data"]["count"] == 1
    assert res["results"][3]["data"]["count"] == 1


def test_batch_read_blocks_write_tools_and_recursion(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    (tmp_path / "ok.txt").write_text("hi\n", encoding="utf-8")

    res = mcp_tools.call_tool("batch_read", {
        "ops": [
            {"id": "bad_write", "tool": "write_file", "args": {"path": "x.txt", "content": "1"}},
            {"id": "bad_exec", "tool": "bash_exec", "args": {"command": "echo 1"}},
            {"id": "bad_rec", "tool": "batch_read", "args": {"ops": []}},
            {"id": "good", "tool": "read_file", "args": {"path": "ok.txt"}},
        ]
    })

    assert res["total"] == 4
    assert res["completed"] == 1
    assert res["results"][0]["ok"] is False and "not allowed" in res["results"][0]["error"]
    assert res["results"][1]["ok"] is False and "not allowed" in res["results"][1]["error"]
    assert res["results"][2]["ok"] is False and "not allowed" in res["results"][2]["error"]
    assert res["results"][3]["ok"] is True


def test_batch_read_enforces_max_ops_and_max_total_chars(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    too_many = mcp_tools.call_tool("batch_read", {
        "ops": [{"tool": "list_dir"} for _ in range(17)]
    })
    assert "error" in too_many

    (tmp_path / "big1.txt").write_text("A" * 2000, encoding="utf-8")
    (tmp_path / "big2.txt").write_text("B" * 2000, encoding="utf-8")
    capped = mcp_tools.call_tool("batch_read", {
        "ops": [
            {"id": "1", "tool": "read_file", "args": {"path": "big1.txt"}},
            {"id": "2", "tool": "read_file", "args": {"path": "big2.txt"}},
        ],
        "max_total_chars": 1500,
    })
    assert capped["truncated"] is True
    assert capped["results"][0]["ok"] is True
    assert capped["results"][1]["ok"] is False
    assert "max_total_chars" in capped["results"][1]["error"]


def test_batch_read_works_in_read_only_mode(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=True)
    (tmp_path / "note.txt").write_text("ro\n", encoding="utf-8")
    try:
        res = mcp_tools.call_tool("batch_read", {
            "ops": [{"id": "r", "tool": "read_file", "args": {"path": "note.txt"}}]
        })
        assert res["completed"] == 1
        assert res["results"][0]["ok"] is True
    finally:
        mcp_tools.configure(read_only=False)
