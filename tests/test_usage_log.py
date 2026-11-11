"""Local usage journal: metadata only, no payload, rotatable and clearable."""

import json
import os

import pytest

from core import mcp_tools, usage


@pytest.fixture()
def workspace(tmp_path):
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )
    yield tmp_path
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )


def _events():
    path = usage.usage_path()
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def test_read_file_is_logged_without_path_or_content(workspace):
    secret = "TOKEN-THAT-MUST-NOT-LEAK"
    sample = workspace / "secret.txt"
    sample.write_text(secret, encoding="utf-8")

    result = mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert "error" not in result
    assert secret in result["stdout"]

    raw = open(usage.usage_path(), encoding="utf-8").read()
    assert secret not in raw
    assert str(sample) not in raw
    events = _events()
    assert events[-1]["tool"] == "read_file"
    assert events[-1]["status"] == "ok"
    assert "args" not in events[-1]


def test_read_only_denial_is_logged_as_denied(workspace):
    mcp_tools.configure(read_only=True)
    mcp_tools.call_tool("bash_exec", {"command": "echo secret-command"})
    events = _events()
    assert events[-1]["status"] == "denied"
    assert events[-1]["error_code"] == "read_only"
    assert "secret-command" not in open(usage.usage_path(), encoding="utf-8").read()


def test_clear_removes_the_journal(workspace):
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert _events()
    removed = usage.clear()
    assert removed >= 1
    assert _events() == []


def test_rotation_keeps_gzipped_generation(workspace, monkeypatch):
    monkeypatch.setenv("MESH_USAGE_MAX_BYTES", "1024")
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    for _ in range(80):
        mcp_tools.call_tool("read_file", {"path": str(sample)})
    base = usage.usage_path()
    assert os.path.exists(base + ".1.gz"), "rotation should archive the previous file"


def test_summary_counts_statuses(workspace):
    sample = workspace / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    mcp_tools.call_tool("read_file", {"path": str(sample)})
    mcp_tools.call_tool("definitely-not-a-tool", {})
    summary = usage.summary(days=1)
    assert summary["events"] >= 2
    assert summary["by_tool"]["read_file"] >= 1
    assert summary["by_status"]["ok"] >= 1
