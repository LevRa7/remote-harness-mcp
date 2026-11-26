"""Unit tests for the batch_edit multiplexer in core/mcp_tools.py."""

from __future__ import annotations

from core import mcp_tools


def test_batch_edit_writes_and_edits_multiple_files(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    res = mcp_tools.call_tool("batch_edit", {
        "ops": [
            {"id": "w1", "op": "write", "path": "a.txt", "content": "hello world\n"},
            {"id": "w2", "op": "write", "path": "sub/b.txt", "content": "foo=1\nbar=2\n"},
            {"id": "e1", "op": "edit", "path": "a.txt", "old_string": "world", "new_string": "mesh"},
            {"id": "e2", "op": "edit", "path": "sub/b.txt", "old_string": "bar=2", "new_string": "bar=42"},
        ]
    })
    assert "error" not in res
    assert res["completed"] == 4
    assert res["failed"] == 0
    assert res["stopped_early"] is False
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello mesh\n"
    assert (tmp_path / "sub" / "b.txt").read_text(encoding="utf-8") == "foo=1\nbar=42\n"


def test_batch_edit_dry_run_validates_without_modifying(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    (tmp_path / "keep.txt").write_text("alpha\n", encoding="utf-8")

    res = mcp_tools.call_tool("batch_edit", {
        "dry_run": True,
        "ops": [
            {"id": "e_ok", "op": "edit", "path": "keep.txt", "old_string": "alpha", "new_string": "beta"},
            {"id": "w_ok", "op": "write", "path": "new.txt", "content": "data"},
        ],
    })
    assert res["completed"] == 2
    assert res["dry_run"] is True
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "alpha\n"
    assert not (tmp_path / "new.txt").exists()


def test_batch_edit_stop_on_error_and_read_only(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    (tmp_path / "x.txt").write_text("abc\n", encoding="utf-8")

    stopped = mcp_tools.call_tool("batch_edit", {
        "stop_on_error": True,
        "ops": [
            {"id": "bad", "op": "edit", "path": "x.txt", "old_string": "NOPE", "new_string": "Z"},
            {"id": "after", "op": "write", "path": "never.txt", "content": "1"},
        ],
    })
    assert stopped["failed"] == 1
    assert stopped["stopped_early"] is True
    assert not (tmp_path / "never.txt").exists()

    mcp_tools.configure(workspace=str(tmp_path), read_only=True)
    try:
        refused = mcp_tools.call_tool("batch_edit", {
            "ops": [{"op": "write", "path": "ro.txt", "content": "1"}]
        })
        assert "error" in refused
    finally:
        mcp_tools.configure(read_only=False)
