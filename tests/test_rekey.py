"""core/rekey.py: rewriting the token everywhere the node keeps it.

The rotation is only useful if the new token lands in every file that can supply
it. ``agent.env`` is common to all platforms, but macOS spells the value in a
launchd plist and Windows bakes it into a Startup ``.vbs`` - and an environment
variable exported by that launcher outranks the file. A rotation that edited only
``agent.env`` would therefore undo itself at the next logon, which is exactly the
kind of bug that only shows up on someone else's machine.
"""

import json
import os
import stat
import sys

import pytest

from core import rekey

ENV_TEXT = """\
# written by the installer
MESH_GATEWAY=mesh.example.com
MESH_USER=node-one
MESH_TOKEN=old-token
MESH_LINK_KEY=lk_abc
"""

PLIST_TEXT = """\
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.remoteharness.mcp.agent</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MESH_TOKEN</key>
        <string>old-token</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

VBS_TEXT = """\
Set WshShell = CreateObject("WScript.Shell")
WshShell.Environment("PROCESS")("MESH_GATEWAY") = "mesh.example.com"
WshShell.Environment("PROCESS")("MESH_USER") = "node-one"
WshShell.Environment("PROCESS")("MESH_TOKEN") = "old-token"
WshShell.CurrentDirectory = "C:\\node"
"""


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "agent.env"
    path.write_text(ENV_TEXT, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# File rewrites
# ---------------------------------------------------------------------------

def test_env_file_rewrite_changes_only_the_token(env_file):
    rekey.write_env_value(str(env_file), "MESH_TOKEN", "new-token")
    text = env_file.read_text(encoding="utf-8")

    assert "MESH_TOKEN=new-token" in text
    assert "old-token" not in text
    assert "MESH_GATEWAY=mesh.example.com" in text
    assert "MESH_USER=node-one" in text
    assert "MESH_LINK_KEY=lk_abc" in text
    assert text.startswith("# written by the installer"), "comments must survive"


def test_env_file_rewrite_appends_a_missing_key(tmp_path):
    path = tmp_path / "agent.env"
    path.write_text("MESH_USER=node-one\n", encoding="utf-8")

    rekey.write_env_value(str(path), "MESH_TOKEN", "new-token")
    assert "MESH_TOKEN=new-token" in path.read_text(encoding="utf-8")


def test_plist_rewrite_touches_only_the_string_after_the_key(tmp_path):
    path = tmp_path / "agent.plist"
    path.write_text(PLIST_TEXT, encoding="utf-8")

    assert rekey.rewrite_plist(str(path), "new-token") is True
    text = path.read_text(encoding="utf-8")

    assert "<string>new-token</string>" in text
    assert "old-token" not in text
    assert "<string>com.remoteharness.mcp.agent</string>" in text, \
        "the Label string must not be mistaken for the token"


def test_plist_without_a_token_key_is_left_alone(tmp_path):
    path = tmp_path / "agent.plist"
    path.write_text(PLIST_TEXT.replace("MESH_TOKEN", "SOMETHING_ELSE"), encoding="utf-8")

    assert rekey.rewrite_plist(str(path), "new-token") is False
    assert "new-token" not in path.read_text(encoding="utf-8")


def test_startup_launcher_rewrite_keeps_utf16_and_other_variables(tmp_path):
    path = tmp_path / "rhm-agent.vbs"
    path.write_text(VBS_TEXT, encoding="utf-16")

    assert rekey.rewrite_startup_launcher(str(path), "new-token") is True
    text = path.read_text(encoding="utf-16")

    assert '("MESH_TOKEN") = "new-token"' in text
    assert "old-token" not in text
    assert '("MESH_GATEWAY") = "mesh.example.com"' in text
    assert rekey.rewrite_startup_launcher(str(path), "third-token") is True
    assert '("MESH_TOKEN") = "third-token"' in path.read_text(encoding="utf-16")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

@pytest.fixture
def wired(env_file, monkeypatch):
    """Point main() at a temp config and stub out everything that leaves the box."""
    calls = {"posts": 0, "restarts": 0, "waits": 0}

    # A MESH_TOKEN exported by another test (or by the developer's shell) outranks
    # the temp config, which would make these assertions depend on the machine.
    monkeypatch.delenv("MESH_TOKEN", raising=False)
    monkeypatch.setattr(rekey, "node_online", lambda gateway, user, timeout=10: True)
    # Never touch the developer's real launchd plist or Startup folder: these
    # lists are per-platform discovery, and a test that edits them would rewrite
    # the machine it runs on.
    monkeypatch.setattr(rekey, "plist_paths", lambda: [])
    monkeypatch.setattr(rekey, "startup_launcher_paths", lambda: [])

    def fake_post(url, token, timeout=20):
        calls["posts"] += 1
        calls["token_used"] = token
        calls["url"] = url
        return {"status": "rotation_pending", "token": "new-token", "username": "node-one"}

    def fake_restart(dry_run=False):
        calls["restarts"] += 1
        return "stub"

    def fake_wait(gateway, user, seconds):
        calls["waits"] += 1
        return calls.get("online_after", True)

    monkeypatch.setattr(rekey, "_post_json", fake_post)
    monkeypatch.setattr(rekey, "restart_agent", fake_restart)
    monkeypatch.setattr(rekey, "wait_online", fake_wait)
    return calls, str(env_file)


def _argv(config, *extra, json_output=False):
    args = ["--config-file", config] + list(extra)
    if json_output:
        args.append("--json")
    return args


def test_dry_run_reports_files_without_writing(wired, env_file, capsys):
    calls, config = wired
    assert rekey.main(_argv(config, "--dry-run", json_output=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry-run"
    assert config in payload["files"]
    assert "old-token" in env_file.read_text(encoding="utf-8")
    assert calls["posts"] == 0
    assert calls["restarts"] == 0


def test_rotation_writes_the_token_and_restarts(wired, env_file, capsys):
    calls, config = wired
    assert rekey.main(_argv(config, json_output=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rotated"
    assert payload["restart"] == "stub"
    assert calls["token_used"] == "old-token"
    assert calls["url"].endswith("/v1/devices/node-one/rotate-token")
    assert "MESH_TOKEN=new-token" in env_file.read_text(encoding="utf-8")


def test_a_node_that_does_not_come_back_gets_the_old_token_restored(wired, env_file, capsys):
    calls, config = wired
    calls["online_after"] = False

    assert rekey.main(_argv(config, json_output=True)) == 4

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rolled-back"
    assert payload["token"] == "new-token", "the pending token is still printed for manual recovery"
    assert "MESH_TOKEN=old-token" in env_file.read_text(encoding="utf-8")
    assert calls["restarts"] == 2, "restart, then restart again after the rollback"


def test_an_offline_node_is_not_rotated(wired, env_file, monkeypatch, capsys):
    calls, config = wired
    monkeypatch.setattr(rekey, "node_online", lambda gateway, user, timeout=10: False)

    assert rekey.main(_argv(config)) == 2
    assert "offline" in capsys.readouterr().err
    assert calls["posts"] == 0


def test_a_node_without_a_token_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MESH_TOKEN", raising=False)
    config = tmp_path / "agent.env"
    config.write_text("MESH_GATEWAY=mesh.example.com\nMESH_USER=node-one\n", encoding="utf-8")

    assert rekey.main(_argv(str(config))) == 2
    assert "standalone" in capsys.readouterr().err
