"""Standalone env-file loading (TSF-02).

The autostart task must not carry the MCP token on the command line - a
``--token=...`` argument is visible in the process list to every user on the
machine.  The node therefore reads ``standalone.env`` the installer already
writes, and keeps the precedence rule the rest of the project uses: the real
environment wins over the file.
"""

import os

from core import server


def _write(tmp_path, text):
    path = tmp_path / "standalone.env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_file_values_are_exported_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_TOKEN", raising=False)
    monkeypatch.delenv("MESH_ALLOWED_HOSTS", raising=False)
    path = _write(tmp_path, "MESH_TOKEN=abc123\nMESH_ALLOWED_HOSTS=node.tailnet.ts.net\n")
    server.apply_standalone_env(path)
    assert os.environ["MESH_TOKEN"] == "abc123"
    assert os.environ["MESH_ALLOWED_HOSTS"] == "node.tailnet.ts.net"


def test_environment_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH_TOKEN", "from-env")
    path = _write(tmp_path, "MESH_TOKEN=from-file\n")
    server.apply_standalone_env(path)
    assert os.environ["MESH_TOKEN"] == "from-env"


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_TOKEN", raising=False)
    server.apply_standalone_env(str(tmp_path / "nope.env"))
    assert "MESH_TOKEN" not in os.environ


def test_non_mesh_keys_are_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("PATH_LIKE", raising=False)
    path = _write(tmp_path, "PATH_LIKE=x\nMESH_TOKEN=t\n")
    server.apply_standalone_env(path)
    assert "PATH_LIKE" not in os.environ


def test_empty_values_are_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_TOKEN", raising=False)
    path = _write(tmp_path, "MESH_TOKEN=\n")
    server.apply_standalone_env(path)
    assert "MESH_TOKEN" not in os.environ
