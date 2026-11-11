"""Shared test isolation: never write the real usage journal during a test run."""

import pytest


@pytest.fixture(autouse=True)
def isolated_usage_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_USAGE_FILE", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("MESH_USAGE_LOG", "1")
    monkeypatch.setenv("MESH_CONTROL_DIR", str(tmp_path / "control"))
    # The product default is "approval"; the legacy unit suite exercises the
    # executor directly, so give it the explicit full-access mode.
    monkeypatch.setenv("MESH_ACCESS_MODE", "full")
    # An approval queued by a test must not make it wait for a person: the
    # blocking-approval test sets its own timeout.
    monkeypatch.setenv("MESH_APPROVAL_WAIT", "0")
    yield
