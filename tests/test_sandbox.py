"""Isolated-container profile: state, path confinement and command argv."""

import os

import pytest

from core import control, mcp_tools, sandbox


@pytest.fixture()
def project(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    mcp_tools.configure(
        workspace=str(workspace),
        jobs_dir=str(tmp_path / "jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )
    control.set_sandbox_state({
        "enabled": True,
        "workspace": str(workspace),
        "runtime": "docker",
        "container": "rhm-sandbox-test",
    })
    yield workspace, outside
    control.set_sandbox_state(None)
    mcp_tools.configure(
        workspace=os.getcwd(),
        jobs_dir=os.path.expanduser("~/.cache/remote-harness-mcp/jobs"),
        read_only=False,
        write_roots=None,
        max_output_chars=12000,
    )


def test_path_inside_project_is_allowed(project):
    workspace, _ = project
    result = mcp_tools.call_tool("read_file", {"path": str(workspace / "inside.txt")})
    assert "inside" in result["stdout"]


def test_path_outside_project_is_refused(project):
    _, outside = project
    result = mcp_tools.call_tool("read_file", {"path": str(outside)})
    assert "error" in result
    assert "isolated project" in result["error"]


def test_container_path_maps_to_the_mount(project):
    workspace, _ = project
    result = mcp_tools.call_tool("read_file", {"path": "/workspace/inside.txt"})
    assert "inside" in result["stdout"]


def test_exec_argv_runs_inside_the_container():
    state = {
        "enabled": True,
        "workspace": os.getcwd(),
        "runtime": "docker",
        "container": "rhm-sandbox-test",
    }
    argv = sandbox.exec_argv("echo hi", state)
    assert argv[:4] == ["docker", "exec", "-i", "-w"]
    assert "/workspace" in argv
    assert argv[-2:] == ["/bin/sh", "-lc"] or argv[-1] == "echo hi"


def test_enable_without_runtime_reports_clearly(monkeypatch):
    monkeypatch.setattr(sandbox, "runtime", lambda: "")
    with pytest.raises(RuntimeError):
        sandbox.enable(os.getcwd())


def test_container_name_is_stable(tmp_path):
    first = sandbox.container_name(str(tmp_path))
    second = sandbox.container_name(str(tmp_path))
    assert first == second
    assert first.startswith("rhm-sandbox-")


def test_system_root_is_refused_as_workspace():
    assert sandbox.workspace_refusal(os.path.abspath(os.sep))
    assert sandbox.workspace_refusal(os.path.expanduser("~"))


def test_networked_isolation_requires_operator_approval():
    from core import settings

    assert settings.requires_approval(
        {"action": "sandbox", "enabled": True, "network": True}
    ) is True
    assert settings.requires_approval(
        {"action": "sandbox", "enabled": True, "network": False}
    ) is False


def test_enable_uses_hardened_flags_and_no_host_mounts(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    captured = []

    class Result:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(sandbox, "runtime", lambda: "docker")
    monkeypatch.setattr(sandbox, "secure_runtime", lambda rt: "")
    monkeypatch.setattr(sandbox, "_image_exists", lambda rt: True)
    monkeypatch.setattr(sandbox, "_container_exists", lambda rt, name: False)
    monkeypatch.setattr(sandbox, "_run", lambda argv, timeout=600, cwd=None: (captured.append(list(argv)) or Result()))
    control.set_sandbox_state(None)

    state = sandbox.enable(str(project))
    assert state["network"] is False
    command = captured[-1]
    assert command[:3] == ["docker", "run", "-d"]
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in command and "no-new-privileges" in command
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--privileged" not in command
    assert any(str(project) in item for item in command)
    # Only the project is mounted: there is no /var/run/docker.sock and no /.
    assert not any("docker.sock" in item for item in command)
    assert sum(1 for item in command if item.startswith("type=bind")) == 1
    control.set_sandbox_state(None)
