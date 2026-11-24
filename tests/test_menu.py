"""Console menu (``rhm``): endpoint, mode switch, log, JSON snapshot."""

import io
import json
import os

import pytest

from core import control, menu, paths


@pytest.fixture(autouse=True)
def isolated_node_config(tmp_path, monkeypatch):
    """Never describe the host's own install: ``rhm`` reports the node, a test

    reports the fixture. Without this the endpoint tests would read the
    machine that runs them.
    """
    monkeypatch.setenv("MESH_CONFIG_DIR", str(tmp_path / "config"))


def test_build_url_from_agent_env(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {
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
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {"MESH_USER": "desktop-01"})
    assert menu.build_mcp_url() == ""


def test_mode_toggles_between_read_only_and_full():
    assert control.next_access_mode("read_only") == "full"
    assert control.next_access_mode("full") == "read_only"
    assert control.next_access_mode("full") == "read_only"


def test_render_contains_mode_endpoint_and_log(monkeypatch, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {
        "MESH_USER": "desktop-01",
        "MESH_LINK_KEY": "lk_" + "b" * 32,
    })
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    (tmp_path / "agent.log").write_text("agent line\n", encoding="utf-8")
    control.set_access_mode("read_only")

    text = menu.render(menu.read_agent_env())
    assert "MCP endpoint" in text
    assert "desktop-01" in text
    assert "Read-only" in text
    assert "agent line" in text


def test_json_snapshot_is_machine_readable(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {"MESH_USER": "n", "MESH_LINK_KEY": "lk_x"})
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
    for line in ("mode:", "workspace:", "isolation:", "remote:", "paused:", "schedules:"):
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


def test_goal_commands_are_disabled_by_default(capsys):
    """A node-wide objective leaks between chats, so it is opt-in."""
    assert menu.main(["goal", "finish the MCP update"]) == 2
    assert "disabled" in capsys.readouterr().err
    assert menu.main(["goal"]) == 2
    assert "disabled" in capsys.readouterr().err


def test_goal_clear_still_works_when_disabled(capsys):
    assert menu.main(["goal", "clear"]) == 0
    assert "cleared" in capsys.readouterr().out


def test_the_approvals_command_is_gone(capsys):
    # Confirmation belongs to the MCP client; the node exposes no approval queue.
    with pytest.raises(SystemExit) as exit_info:
        menu.main(["approvals"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_render_hides_the_disabled_goal(monkeypatch, tmp_path):
    monkeypatch.delenv("MESH_GOAL", raising=False)
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {"MESH_USER": "n"})
    monkeypatch.setattr(menu, "agent_log_path", lambda: str(tmp_path / "agent.log"))
    text = menu.render(menu.read_agent_env())
    assert "Goal:" not in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_is_executable():
    launcher = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "rhm")
    assert os.path.exists(launcher)


def test_keys_is_a_context_manager(monkeypatch):
    """`rhm` used to crash with TypeError: Keys has no __exit__."""
    restored = []
    monkeypatch.setattr(menu.Keys, "_enable_ansi", lambda self: None)
    monkeypatch.setattr(menu.Keys, "restore", lambda self: restored.append(True))
    with menu.Keys() as keys:
        assert isinstance(keys, menu.Keys)
    assert restored == [True]


def test_render_keeps_the_full_text_for_scripts(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {
        "MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32})
    text = menu.render(menu.read_agent_env())
    assert "MCP endpoint:" in text
    assert "Agent log (" in text


def test_interactive_frame_fits_the_window(monkeypatch):
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    frame = menu._frame(env, columns=60, rows=12)
    lines = frame.splitlines()
    assert len(lines) <= 12, "a frame taller than the window scrolls the screen"
    assert all(len(line) <= 60 for line in lines), "wrapped lines smear the menu"
    assert "q quit" in frame


def test_virtual_keys_are_layout_independent():
    assert menu._key_from_vk(0x44) == "d"          # physical D
    assert menu._key_from_vk(0x44, shift=True) == "D"
    assert menu._key_from_vk(0x25) == "left"
    assert menu._key_from_vk(0xBF) == "/"
    assert menu._key_from_vk(0xBF, shift=True) == "?"
    assert menu._key_from_vk(0x37, shift=True, uchar="?") == "?"


def test_cyrillic_character_maps_to_the_latin_key():
    assert menu._layout_key("в") == "d"
    assert menu._layout_key("В") == "D"
    assert menu._layout_key("d") == "d"


def test_home_line_has_an_edit_button(monkeypatch):
    monkeypatch.setattr(menu, "read_agent_env", lambda *args, **kwargs: {"MESH_USER": "n"})
    text = menu.render(menu.read_agent_env())
    assert "Home:" in text
    assert "[d] edit" in text


def test_home_edit_button_survives_a_narrow_frame():
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    frame = menu._frame(env, columns=60, rows=24)
    assert "[d] edit" in frame
    assert all(len(line) <= 60 for line in frame.splitlines())


def test_frame_shows_token_and_qr_in_a_large_window():
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    frame = menu._frame(env, columns=120, rows=40)
    assert "Token: lk_" in frame
    assert "\u2588" in frame, "the QR code belongs in a window that fits it"


def test_frame_shows_token_and_qr_hint_in_a_small_window():
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    frame = menu._frame(env, columns=80, rows=24)
    assert "Token: lk_" in frame
    assert "QR: press e" in frame


def test_draw_repaints_only_when_the_frame_changes(monkeypatch):
    env = {"MESH_USER": "node-one", "MESH_LINK_KEY": "lk_" + "a" * 32,
           "MESH_GATEWAY": "gw.test"}
    painted = []
    monkeypatch.setattr(menu, "_terminal_size", lambda: (80, 24))
    monkeypatch.setattr(menu, "_paint", painted.append)
    menu.draw(env)
    menu.draw(env)
    assert len(painted) == 1, "an unchanged frame must not be repainted"
    menu.draw(env, force=True)
    assert len(painted) == 2
    assert "Token: lk_" in painted[-1], "the live token belongs in the frame"


def test_paint_falls_back_to_ansi(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(menu, "_ANSI", True)
    monkeypatch.setattr(menu, "_WIN_CONSOLE", False)
    monkeypatch.setattr(menu.sys, "stdout", out)
    menu._paint("hello")
    written = out.getvalue()
    assert written.startswith("\x1b[H")
    assert written.endswith("\x1b[J")


# ---------------------------------------------------------------------------
# The endpoint belongs to the installed node, not to what an older install left
# ---------------------------------------------------------------------------

def _write_env(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join("%s=%s\n" % item for item in values.items())
    path.write_text(body, encoding="utf-8")


def _write_state(directory, **state):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "autostart.state.json").write_text(json.dumps(state), encoding="utf-8")


def _funnel_config(tmp_path):
    """The machine this bug was reported on: a Funnel service plus stale tunnel files."""
    config = tmp_path / "config"
    _write_state(config, kind="service", mode="standalone", port=8096,
                 funnel="matebook16.merino-carob.ts.net")
    _write_env(config / "standalone.env", {
        "MESH_TOKEN": "tok-standalone",
        "MESH_ALLOWED_HOSTS": "matebook16.merino-carob.ts.net",
        "MESH_PUBLIC_URL": "https://matebook16.merino-carob.ts.net",
    })
    # Left behind by the tunnel-era install: a bridge this node is not dialled
    # through. A URL built from these files has no tunnel into the machine.
    _write_env(config / "domain.env", {
        "MESH_PUBLIC_URL": "https://racknerd-5a24bf9.merino-carob.ts.net"})
    _write_env(config / "agent.env", {
        "MESH_GATEWAY": "racknerd-5a24bf9.merino-carob.ts.net",
        "MESH_USER": "matebook16",
        "MESH_LINK_KEY": "lk_" + "c" * 32,
    })
    return config


def test_a_funnel_node_publishes_its_own_address(tmp_path):
    _funnel_config(tmp_path)
    info = menu.node_endpoint()
    assert info["mode"] == "standalone"
    assert info["url"] == "https://matebook16.merino-carob.ts.net/sse?token=tok-standalone"
    assert "racknerd" not in info["url"], "the bridge has no tunnel into a Funnel node"
    assert info["token"] == "tok-standalone"
    assert info["node"] == "matebook16"


def test_the_menu_prints_the_funnel_endpoint(tmp_path):
    _funnel_config(tmp_path)
    text = menu.render({"MESH_USER": "matebook16",
                        "MESH_GATEWAY": "racknerd-5a24bf9.merino-carob.ts.net",
                        "MESH_LINK_KEY": "lk_" + "c" * 32})
    assert "https://matebook16.merino-carob.ts.net/sse?token=tok-standalone" in text
    assert "Node: matebook16" in text
    assert "racknerd" not in text


def test_a_tunnel_node_keeps_the_gateway_shape(tmp_path):
    config = tmp_path / "config"
    _write_state(config, kind="logon", mode="tunnel", port=8096)
    _write_env(config / "agent.env", {"MESH_USER": "desktop-01",
                                      "MESH_LINK_KEY": "lk_" + "d" * 32})
    _write_env(config / "domain.env", {"MESH_PUBLIC_URL": "https://mesh.example"})
    info = menu.node_endpoint()
    assert info["mode"] == "tunnel"
    assert info["url"] == "https://mesh.example/sse?user=desktop-01&token=lk_" + "d" * 32


def test_a_standalone_env_without_installer_state_is_still_standalone(tmp_path):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    _write_env(config / "standalone.env", {"MESH_TOKEN": "tok",
                                           "MESH_ALLOWED_HOSTS": "node.tailnet.ts.net"})
    info = menu.node_endpoint()
    assert info["mode"] == "standalone"
    assert info["url"] == "https://node.tailnet.ts.net/sse?token=tok"


def test_the_installer_state_decides_over_a_leftover_standalone_env(tmp_path):
    config = tmp_path / "config"
    _write_state(config, kind="logon", mode="tunnel")
    _write_env(config / "standalone.env", {"MESH_TOKEN": "tok"})
    _write_env(config / "agent.env", {"MESH_USER": "n", "MESH_LINK_KEY": "lk_x",
                                      "MESH_GATEWAY": "gw.example"})
    info = menu.node_endpoint()
    assert info["mode"] == "tunnel", "the installer record wins over an orphan file"


def test_the_recorded_funnel_host_names_the_node_when_no_url_is_written(tmp_path):
    config = tmp_path / "config"
    _write_state(config, kind="service", mode="standalone", port=8096, funnel="pc.tailnet.ts.net")
    _write_env(config / "standalone.env", {"MESH_TOKEN": "tok"})
    info = menu.node_endpoint()
    assert info["url"] == "https://pc.tailnet.ts.net/sse?token=tok"


def test_a_funnel_node_without_a_token_has_no_url(tmp_path):
    config = tmp_path / "config"
    _write_state(config, kind="service", mode="standalone", funnel="pc.tailnet.ts.net")
    info = menu.node_endpoint()
    assert info["url"] == ""
    assert "token" in info["hint"].lower()


def test_a_loopback_name_never_becomes_a_public_endpoint(tmp_path):
    config = tmp_path / "config"
    _write_state(config, mode="standalone", port=8096, funnel="127.0.0.1")
    _write_env(config / "standalone.env", {"MESH_TOKEN": "tok",
                                           "MESH_ALLOWED_HOSTS": "127.0.0.1, localhost"})
    info = menu.node_endpoint()
    assert info["url"] == "http://127.0.0.1:8096/sse?token=tok"


def test_a_service_install_is_described_before_the_per_user_leftovers(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_CONFIG_DIR", raising=False)
    per_user = tmp_path / "home" / "config"
    legacy = tmp_path / "home" / "legacy"
    service = tmp_path / "programdata"
    _write_env(per_user / "agent.env", {"MESH_USER": "old", "MESH_LINK_KEY": "lk_old",
                                        "MESH_GATEWAY": "racknerd-5a24bf9.merino-carob.ts.net"})
    legacy.mkdir(parents=True)
    _write_env(service / "service.env", {"MESH_CONFIG_DIR": str(service),
                                         "MESH_SERVICE_MODE": "server"})
    _write_state(service, kind="service", mode="standalone", port=8096,
                 funnel="matebook16.merino-carob.ts.net")
    _write_env(service / "standalone.env", {"MESH_TOKEN": "tok-service",
                                            "MESH_PUBLIC_URL": "https://matebook16.merino-carob.ts.net"})
    monkeypatch.setattr(paths, "config_dir", lambda: str(per_user))
    monkeypatch.setattr(paths, "legacy_config_dir", lambda: str(legacy))
    monkeypatch.setattr(paths, "service_config_dir", lambda: str(service))
    assert menu.install_dir() == str(service)
    info = menu.node_endpoint()
    assert info["url"] == "https://matebook16.merino-carob.ts.net/sse?token=tok-service"
    assert info["node"] == "matebook16"

# ---------------------------------------------------------------------------
# A live standalone node is ONLINE without a heartbeat to read
# ---------------------------------------------------------------------------

def test_a_live_standalone_node_is_online_without_a_heartbeat(tmp_path, monkeypatch):
    """The service node runs under another account and writes no heartbeat, so the
    menu called it OFFLINE while it was answering Gemini. Its loopback port is the
    proof that does not depend on the account or the directory."""
    _funnel_config(tmp_path)
    monkeypatch.setattr(menu, "_probe_health", lambda port, timeout=0.6: int(port) == 8096)
    monkeypatch.setattr(menu, "_ALIVE_CACHE", {})
    info = menu.node_endpoint()
    assert info["port"] == 8096, "the installer records the port the node listens on"
    assert menu.node_online(info, {"online": False}) is True
    assert "Status: ONLINE" in menu.render({})


def test_a_dead_standalone_node_is_offline(tmp_path, monkeypatch):
    _funnel_config(tmp_path)
    monkeypatch.setattr(menu, "_probe_health", lambda *args, **kwargs: False)
    monkeypatch.setattr(menu, "_ALIVE_CACHE", {})
    info = menu.node_endpoint()
    assert menu.node_online(info, {"online": False}) is False
    assert "Status: OFFLINE" in menu.render({})


def test_a_tunnel_node_still_trusts_only_the_heartbeat(tmp_path, monkeypatch):
    config = tmp_path / "config"
    _write_state(config, kind="logon", mode="tunnel", port=8096)
    _write_env(config / "agent.env", {"MESH_USER": "n", "MESH_LINK_KEY": "lk_x"})
    monkeypatch.setattr(menu, "_probe_health",
                        lambda *args, **kwargs: pytest.fail("no local server to probe"))
    info = menu.node_endpoint()
    assert menu.node_online(info, {"online": False}) is False
    assert menu.node_online(info, {"online": True}) is True


def test_the_health_answer_is_cached_for_a_few_seconds(monkeypatch):
    calls = []

    def probe(port, timeout=0.6):
        calls.append(port)
        return True

    monkeypatch.setattr(menu, "_probe_health", probe)
    monkeypatch.setattr(menu, "_ALIVE_CACHE", {})
    assert menu._standalone_alive(8096, now=100.0) is True
    assert menu._standalone_alive(8096, now=100.5) is True
    assert calls == [8096], "a repaint must not pay for another probe"
    assert menu._standalone_alive(8096, now=100.0 + menu._ALIVE_TTL + 1) is True
    assert len(calls) == 2, "a stale answer is re-checked"
