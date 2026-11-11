"""Console menu (``rhm``): endpoint, mode switch, log, JSON snapshot."""

import json
import os

import pytest

from core import control, menu


def test_build_url_from_agent_env(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {
        "MESH_USER": "desktop-01",
        "MESH_LINK_KEY": "lk_" + "a" * 32,
    })
    url = menu.build_mcp_url()
    assert "/sse?user=desktop-01&token=lk_" in url


def test_qr_lines_render_when_encoder_is_available():
    pytest.importorskip("qrcode")
    lines = menu.qr_lines("https://example.test/sse?user=n&token=lk_x")
    assert lines
    assert any("\u2588" in line or "\u2584" in line or "\u2580" in line for line in lines)


def test_missing_link_key_has_no_url(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "desktop-01"})
    assert menu.build_mcp_url() == ""


def test_mode_cycles_read_only_approval_full():
    assert control.next_access_mode("read_only") == "approval"
    assert control.next_access_mode("approval") == "full"
    assert control.next_access_mode("full") == "read_only"


def test_render_contains_mode_endpoint_and_log(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {
        "MESH_USER": "desktop-01",
        "MESH_LINK_KEY": "lk_" + "b" * 32,
    })
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    (tmp_path / "agent.log").write_text("agent line\n", encoding="utf-8")
    control.set_access_mode("approval")

    text = menu.render(menu.read_agent_env())
    assert "MCP endpoint" in text
    assert "desktop-01" in text
    assert "Access on request" in text
    assert "agent line" in text


def test_json_snapshot_is_machine_readable(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n", "MESH_LINK_KEY": "lk_x"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    menu.main(["--json"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["mode"] in control.ACCESS_MODES
    assert "url" in payload
    assert "log" in payload


# ---------------------------------------------------------------------------
# One-shot subcommands: the same switches as the menu, without a terminal
# ---------------------------------------------------------------------------

def test_mode_subcommand_changes_the_mode(capsys):
    assert menu.main(["mode", "read_only"]) == 0
    assert control.access_mode() == "read_only"
    assert "read_only" in capsys.readouterr().out


def test_cd_subcommand_sets_the_workspace(tmp_path, capsys):
    target = tmp_path / "project"
    target.mkdir()
    assert menu.main(["cd", str(target)]) == 0
    assert control.workspace() == os.path.realpath(str(target))


def test_status_subcommand_prints_every_switch(capsys):
    assert menu.main(["status"]) == 0
    text = capsys.readouterr().out
    for line in ("mode:", "workspace:", "isolation:", "remote:", "paused:", "approvals:"):
        assert line in text


def test_status_json_is_machine_readable(capsys):
    assert menu.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert "remote_control" in payload
    assert "isolation" in payload


def test_remote_control_can_be_closed_and_reopened_from_the_command_line(capsys):
    assert menu.main(["remote-control", "off"]) == 0
    assert control.remote_control_enabled() is False
    assert "only" in capsys.readouterr().out.lower()

    assert menu.main(["remote-control", "on"]) == 0
    assert control.remote_control_enabled() is True


def test_a_bad_value_is_refused_with_exit_code_2(capsys):
    assert menu.main(["mode", "root"]) == 2
    assert "mode must be one of" in capsys.readouterr().err
    assert menu.main(["isolation", "maybe"]) == 2


def test_pause_and_resume_subcommands(capsys):
    assert menu.main(["pause"]) == 0
    assert control.paused() is True
    assert menu.main(["resume"]) == 0
    assert control.paused() is False


def test_goal_command_sets_shows_and_closes(capsys):
    assert menu.main(["goal", "finish the MCP update"]) == 0
    assert "finish the MCP update" in capsys.readouterr().out

    assert menu.main(["goal"]) == 0
    shown = capsys.readouterr().out
    assert "finish the MCP update" in shown
    assert "active" in shown

    assert menu.main(["goal", "done", "shipped"]) == 0
    closed = capsys.readouterr().out
    assert "Goal: none" in closed
    assert "done" in closed


def test_goal_set_without_text_is_refused(capsys):
    assert menu.main(["goal", "set"]) == 2
    assert "needs the objective" in capsys.readouterr().err


def test_approvals_command_allows_and_revokes(capsys):
    assert menu.main(["approvals"]) == 0
    assert "ask on every" in capsys.readouterr().out

    assert menu.main(["approvals", "allow"]) == 0
    assert "ALL allowed" in capsys.readouterr().out
    assert control.grant_status()["active"] is True

    assert menu.main(["approvals", "off"]) == 0
    assert control.grant_status()["active"] is False
    assert "ask on every" in capsys.readouterr().out


def test_approvals_allow_for_a_limited_time(capsys):
    assert menu.main(["approvals", "allow", "30"]) == 0
    status = control.grant_status()
    assert status["active"] is True
    assert 1500 < status["expires_in"] <= 1800
    assert "min" in capsys.readouterr().out


def test_render_shows_the_approval_state(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    menu.main(["approvals", "allow"])
    text = menu.render(menu.read_agent_env())
    assert "Approvals: ALL allowed" in text
    menu.main(["approvals", "off"])


def test_render_shows_the_goal_line(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda: {"MESH_USER": "n"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    menu.main(["goal", "keep the node updated"])
    text = menu.render(menu.read_agent_env())
    assert "Goal: keep the node updated" in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_is_executable():
    launcher = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "rhm")
    assert os.path.exists(launcher)
