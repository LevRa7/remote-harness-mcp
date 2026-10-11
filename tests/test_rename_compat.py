"""The rename to *Remote Harness MCP* must not break an installed node.

The project shipped as "Antigravity Mesh" (``antigravity-mesh``, ``agy-*`` units,
``AntigravityMesh-Setup-*.exe``, the ``gemini-computer-use`` npm package). These
tests pin the compatibility half of the rename:

* ``core/paths.py`` prefers the new directory name and falls back to the
  pre-rename one when that is the only one on the host, so an upgraded node keeps
  its token, domain file, job history and update journal;
* the installer retires the old supervisors instead of leaving a second agent
  running;
* the updater and the webhook relay still understand the old asset names,
  environment variables, routes and headers.

A rename that loses a node's configuration is worse than no rename at all, so
these are hard gates like any other suite.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

from core import paths

REPO = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Runtime directories: the new name wins, the old one keeps an install alive
# ---------------------------------------------------------------------------

def test_paths_declare_one_new_and_one_legacy_name():
    assert paths.APP_NAME == "remote-harness-mcp"
    assert paths.LEGACY_APP_NAME == "antigravity-mesh"


def test_config_dir_prefers_the_new_name_when_both_exist(monkeypatch, tmp_path):
    (tmp_path / ".config" / paths.APP_NAME).mkdir(parents=True)
    (tmp_path / ".config" / paths.LEGACY_APP_NAME).mkdir(parents=True)
    monkeypatch.setattr(paths, "home_dir", lambda: str(tmp_path))
    assert paths.config_dir() == str(tmp_path / ".config" / paths.APP_NAME)


def test_config_dir_falls_back_to_the_pre_rename_directory(monkeypatch, tmp_path):
    (tmp_path / ".config" / paths.LEGACY_APP_NAME).mkdir(parents=True)
    monkeypatch.setattr(paths, "home_dir", lambda: str(tmp_path))
    assert paths.config_dir() == str(tmp_path / ".config" / paths.LEGACY_APP_NAME)


def test_config_dir_creates_the_new_name_on_a_fresh_host(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "home_dir", lambda: str(tmp_path))
    assert paths.config_dir() == str(tmp_path / ".config" / paths.APP_NAME)


def test_cache_dir_falls_back_to_the_pre_rename_directory(monkeypatch, tmp_path):
    (tmp_path / ".cache" / paths.LEGACY_APP_NAME).mkdir(parents=True)
    monkeypatch.setattr(paths, "home_dir", lambda: str(tmp_path))
    assert paths.cache_dir() == str(tmp_path / ".cache" / paths.LEGACY_APP_NAME)
    assert paths.cache_path("jobs") == str(tmp_path / ".cache" / paths.LEGACY_APP_NAME / "jobs")


def test_the_runtime_modules_resolve_their_directories_through_paths():
    """No module may hardcode a runtime directory name of its own."""
    for relative in ("core/agent.py", "core/updater.py", "core/mcp_tools.py",
                     "core/web_share.py", "core/device.py", "core/domain.py"):
        text = _read(relative)
        assert re.search(r"^\s*from core import .*\bpaths\b", text, re.M) or \
            re.search(r"^\s*from core import paths\b", text, re.M), relative
        assert re.search(r'"\.config",\s*"antigravity-mesh"', text) is None, relative
        assert re.search(r'"\.cache",\s*"antigravity-mesh"', text) is None, relative


def test_the_shell_installer_resolves_the_same_two_names():
    text = _read("install.sh")
    assert 'CONFIG_DIR="$HOME/.config/%s"' % paths.APP_NAME in text
    assert 'LEGACY_CONFIG_DIR="$HOME/.config/%s"' % paths.LEGACY_APP_NAME in text
    assert "/etc/%s" % paths.APP_NAME in text


def test_the_powershell_installer_resolves_the_same_two_names():
    text = _read("install.ps1")
    assert ".config\\%s" % paths.APP_NAME in text
    assert ".config\\%s" % paths.LEGACY_APP_NAME in text


# ---------------------------------------------------------------------------
# The installer retires the pre-rename supervisors
# ---------------------------------------------------------------------------

def test_installer_defines_and_calls_the_legacy_retirement():
    text = _read("install.sh")
    assert "retire_legacy_autostart()" in text
    definition = text.index("retire_legacy_autostart()")
    call = text.index("\nretire_legacy_autostart\n")
    assert definition < call, "the retirement helper must be defined before it is called"
    for legacy in ("agy-agent.service", "agy-standalone.service",
                   "com.antigravity.mesh.agent", "com.antigravity.mesh.standalone"):
        assert legacy in text, legacy
    assert 'rhm-agent.service' in text


def test_windows_installer_retires_the_pre_rename_tasks_and_startup_entry():
    text = _read("install.ps1")
    assert "AntigravityMeshWatchdog" in text and "AntigravityMeshUpdater" in text
    assert "Unregister-ScheduledTask" in text
    assert "agy-agent.vbs" in text
    assert "RemoteHarnessMCPWatchdog" in text


# ---------------------------------------------------------------------------
# The updater: pre-rename assets and supervisors stay usable
# ---------------------------------------------------------------------------

def test_choose_asset_accepts_the_pre_rename_setup_name():
    from core import updater

    release = {
        "version": "0.4.4",
        "assets": [{"name": "AntigravityMesh-Setup-0.4.4.exe", "url": "https://x/y.exe", "size": 10}],
    }
    chosen = updater.choose_asset(release, platform="win32")
    assert chosen is not None
    assert chosen["kind"] == "setup"
    assert chosen["name"] == "AntigravityMesh-Setup-0.4.4.exe"


def test_choose_asset_prefers_the_current_setup_name():
    from core import updater

    release = {
        "version": "0.4.4",
        "assets": [
            {"name": "AntigravityMesh-Setup-0.4.4.exe", "url": "https://x/old.exe", "size": 10},
            {"name": "RemoteHarnessMCP-Setup-0.4.4.exe", "url": "https://x/new.exe", "size": 11},
        ],
    }
    chosen = updater.choose_asset(release, platform="win32")
    assert chosen["name"] == "RemoteHarnessMCP-Setup-0.4.4.exe"


@pytest.mark.skipif(sys.platform == "darwin", reason="the systemd branch is Linux-only")
def test_restart_uses_the_pre_rename_systemd_unit(monkeypatch, tmp_path):
    from core import updater

    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "agy-agent.service").write_text("[Unit]\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return 0, ""

    monkeypatch.setattr(updater, "_run_capture", fake_run)
    monkeypatch.setattr(updater, "wait_for_node", lambda *a, **k: True)

    result = updater._restart_posix(str(tmp_path), {}, 0)
    assert result["ok"] is True and result["method"] == "systemctl"
    assert calls and "agy-agent.service" in calls[0]


def test_the_updater_keeps_a_legacy_asset_and_unit_name_declared():
    text = _read("core/updater.py")
    assert 'LEGACY_SETUP_ASSET = "AntigravityMesh-Setup-%s.exe"' in text
    assert 'LEGACY_SYSTEMD_UNIT = "agy-agent.service"' in text
    assert 'LEGACY_LAUNCHD_LABEL = "com.antigravity.mesh.agent"' in text
    assert "core/paths.py" in text  # a payload without it is not a node


# ---------------------------------------------------------------------------
# The gateway, the legacy webhook server and the Apps Script relay
# ---------------------------------------------------------------------------

def test_gateway_reads_the_pre_rename_registry_when_it_is_the_only_one():
    text = _read("gateway.py")
    assert 'LEGACY_REGISTRY_PATH = "/etc/antigravity-mesh/registry.json"' in text
    assert "REGISTRY_PATH = LEGACY_REGISTRY_PATH" in text


def test_webhook_server_keeps_the_pre_rename_env_route_and_header():
    text = _read("rhm_webhook_server.py")
    assert 'os.environ.get("AGY_WEBHOOK_PORT"' in text
    assert 'os.environ.get("RHM_WEBHOOK_PORT")' in text
    assert 'os.environ.get("AGY_WEBHOOK_HOST"' in text
    assert 'os.environ.get("RHM_WEBHOOK_HOST")' in text
    assert '"/api/agy"' in text, "the pre-rename route must still be served"
    assert 'X-Agy-Token' in text
    assert "ANTIGRAVITY_CLI_BIN" in text


def test_apps_script_relay_reads_both_property_spellings():
    text = _read("google_apps_script_relay.js")
    assert "props.getProperty('RHM_SECRET_TOKEN')" in text
    assert "props.getProperty('AGY_SECRET_TOKEN')" in text
    assert "props.getProperty('RHM_ENDPOINT_BASE')" in text
    assert "props.getProperty('AGY_ENDPOINT_BASE')" in text


# ---------------------------------------------------------------------------
# Identity: the published names are the new ones
# ---------------------------------------------------------------------------

def test_package_json_is_renamed_and_keeps_the_old_binary_alias():
    data = json.loads(_read("package.json"))
    assert data["name"] == paths.APP_NAME
    assert "remote-harness-mcp" in data["bin"]
    assert data["bin"]["gemini-computer-use"] == "bin/cli.js", (
        "an existing npm install must keep its CLI command")
    for url in (data["repository"]["url"], data["bugs"]["url"], data["homepage"]):
        assert "remote-harness-mcp" in url
        assert "Computer-use-for-Gemini-App-Web" not in url


def test_version_module_points_at_the_renamed_repository():
    from core import version as version_module

    assert version_module.REPO_SLUG == "LevRa7/remote-harness-mcp"


def test_no_old_identity_survives_in_the_shipped_identity_files():
    """The files that *name* the project must not carry the old names at all.

    ``package.json`` is checked by :func:`test_package_json_is_renamed_and_keeps_the_old_binary_alias`:
    it keeps ``gemini-computer-use`` on purpose, as the pre-rename CLI alias.
    """
    old_names = ("Antigravity Mesh", "AntigravityMesh", "antigravity-mesh",
                 "gemini-computer-use", "Computer-use-for-Gemini-App-Web")
    for relative in ("core/version.py", "bin/cli.js",
                     ".github/workflows/ci.yml", ".github/workflows/release.yml"):
        text = _read(relative)
        for old in old_names:
            assert old not in text, "%s still names %r" % (relative, old)


def test_the_release_workflow_publishes_the_new_installer_name():
    text = _read(".github/workflows/release.yml")
    assert "RemoteHarnessMCP-Setup-" in text


def test_readme_names_the_project():
    assert "Remote Harness MCP" in _read("README.md").splitlines()[0]
