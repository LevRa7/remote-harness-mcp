"""Security hotfixes for REV-SEC-13: the read-only switch must cover the shell.

Before this test existed, ``MESH_READ_ONLY=1`` refused ``write_file``/``edit_file``
but still executed ``bash_exec`` and ``run_job``.  A read-only node could therefore
install packages, delete files or open a reverse shell.  The guards live in both
``call_tool`` (so every path is covered) and the individual handlers (so a direct
call cannot bypass the dispatcher).
"""

import os
import sys

import pytest

from core import mcp_tools


def py_command(code: str) -> str:
    """Run *code* with the test interpreter in the host shell."""
    exe = sys.executable
    if os.name == "nt":
        return '& "%s" -c "%s"' % (exe.replace('"', '`"'), code)
    return "'%s' -c \"%s\"" % (exe.replace("'", "'\\''"), code)


@pytest.fixture()
def read_only(tmp_path):
    mcp_tools.configure(
        workspace=str(tmp_path),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=True,
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


def test_read_only_blocks_bash_exec_without_running_it(read_only):
    marker = read_only / "pwned.txt"
    result = mcp_tools.call_tool(
        "bash_exec",
        {"command": py_command("open(%r, 'w').write('owned')" % str(marker))},
    )
    assert "error" in result
    assert "read-only" in result["error"].lower()
    assert not marker.exists()


def test_read_only_blocks_direct_bash_handler(read_only):
    marker = read_only / "direct.txt"
    result = mcp_tools._tool_bash_exec(
        {"command": py_command("open(%r, 'w').write('owned')" % str(marker))}
    )
    assert "error" in result
    assert not marker.exists()


def test_read_only_blocks_run_job_without_spawning(read_only):
    marker = read_only / "job.txt"
    result = mcp_tools.call_tool(
        "run_job",
        {"command": py_command("open(%r, 'w').write('owned')" % str(marker))},
    )
    assert "error" in result
    assert "read-only" in result["error"].lower()
    assert not marker.exists()


def test_read_only_blocks_unshare(read_only, monkeypatch):
    monkeypatch.setattr(mcp_tools, "_web_share", object(), raising=False)
    result = mcp_tools.call_tool("unshare", {"name": "demo"})
    assert "error" in result
    assert "read-only" in result["error"].lower()


def test_read_only_still_allows_file_inspection(read_only):
    sample = read_only / "notes.txt"
    sample.write_text("hello read-only\n", encoding="utf-8")

    read = mcp_tools.call_tool("read_file", {"path": str(sample)})
    assert "error" not in read
    assert "hello read-only" in read["stdout"]

    listing = mcp_tools.call_tool("list_dir", {"path": str(read_only)})
    assert "error" not in listing
    assert "notes.txt" in listing["stdout"]

    found = mcp_tools.call_tool("grep_search", {"pattern": "hello", "path": str(read_only)})
    assert "error" not in found
    assert any("notes.txt" in match["path"] for match in found["matches"])


def test_read_only_denial_is_stable_and_actionable(read_only):
    result = mcp_tools.call_tool("bash_exec", {"command": "echo hi"})
    assert result["error"] == (
        "This node is read-only: only observation tools are allowed; commands and "
        "writes are disabled"
    )
