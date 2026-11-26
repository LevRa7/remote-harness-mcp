"""Unit tests for the run_script single-call runner in core/mcp_tools.py."""

from __future__ import annotations

from core import mcp_tools


def test_run_script_python_foreground_and_cleanup(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    res = mcp_tools.call_tool("run_script", {
        "lang": "python",
        "code": "import os; print('SUM:', 21 + 21); print('CWD_OK:', os.path.isdir('.'))",
    })
    assert "error" not in res
    assert res["exit_code"] == 0
    assert "SUM: 42" in res["stdout"]
    assert "CWD_OK: True" in res["stdout"]


def test_run_script_background_hands_off_to_run_job(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=False)
    res = mcp_tools.call_tool("run_script", {
        "lang": "python",
        "background": True,
        "code": "print('BG_DONE')",
    })
    assert "error" not in res
    assert res["background"] is True
    assert res["job_id"]
    out = mcp_tools.call_tool("job_output", {"job_id": res["job_id"], "wait_ms": 5000})
    assert "BG_DONE" in out["stdout"]


def test_run_script_refused_in_read_only(tmp_path):
    mcp_tools.configure(workspace=str(tmp_path), read_only=True)
    try:
        res = mcp_tools.call_tool("run_script", {"code": "print(1)"})
        assert "error" in res
    finally:
        mcp_tools.configure(read_only=False)
