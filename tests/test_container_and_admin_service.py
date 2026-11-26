"""The container install and the administrator-service variant must stay honest.

Two additions are pinned here:

* **container** - `install.sh --container` (and the automatic Docker/LXC/podman
  detection) must not write a systemd unit, must keep `agent.env` on a volume and
  must hand the agent to the foreground when the image entrypoint asks it to;
  `docker/` carries the image, the compose file and the entrypoint that do it.
* **administrator service** - `install.ps1 -AdminService` registers one machine
  task (SYSTEM, highest run level, boot trigger) instead of the per-user Startup
  entry, keeps the configuration in ProgramData, and the wizard offers it as a
  variant that still goes through `install.ps1`.

The tests are deliberately source- and behaviour-level: they must fail if the
variant is only half wired (for example a wizard mode that never passes
`-AdminService`, or a service task that forgets the machine configuration).
"""

import json
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")
INSTALL_PS1 = os.path.join(REPO, "install.ps1")
GUI = os.path.join(REPO, "install-gui.ps1")
STRINGS = os.path.join(REPO, "install-gui.strings.json")
SERVICE = os.path.join(REPO, "ops", "windows", "rhm-service.ps1")
DOCKER = os.path.join(REPO, "docker")


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Container installation
# ---------------------------------------------------------------------------

def test_shell_installer_accepts_the_container_flags():
    text = _read(INSTALL_SH)
    assert "--container|--docker)" in text, "the explicit flag must exist"
    assert "--data-dir=*)" in text, "agent.env must be relocatable onto a volume"
    assert "--foreground)" in text, "the entrypoint needs an in-process agent"
    assert "CONTAINER_MODE=false" in text
    assert "[--container|--docker] [--data-dir=<path>] [--foreground]" in text, (
        "the flags must be documented where the operator looks for them")


def test_container_mode_is_detected_not_guessed():
    text = _read(INSTALL_SH)
    assert "/.dockerenv" in text
    assert "/proc/1/cgroup" in text
    # Detection must only ever switch the mode ON.
    assert 'if [ "$CONTAINER_DETECTED" = true ]; then' in text
    assert 'CONTAINER_MODE=true' in text


def test_container_branch_precedes_systemd_in_both_install_branches():
    """A container has no systemd user session: the unit must not be written."""
    text = _read(INSTALL_SH)
    assert text.count('if [ "$CONTAINER_MODE" = true ]; then') >= 2, (
        "both the standalone and the tunnel branch need the container branch")
    tunnel = text[text.index("BRANCH: CLOUD GATEWAY"):]
    assert tunnel.index('if [ "$CONTAINER_MODE" = true ]; then') < tunnel.index(
        'rhm-agent.service'), "the unit is written before the container branch"


def test_foreground_exec_keeps_the_agent_as_the_container_process():
    text = _read(INSTALL_SH)
    assert 'exec "$PYTHON_BIN" -u -m core.agent' in text
    assert 'exec "$PYTHON_BIN" -u -m core.server' in text


def test_image_assets_exist_and_point_at_the_installer():
    dockerfile = _read(os.path.join(DOCKER, "Dockerfile"))
    assert "FROM python:" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "docker/entrypoint.sh" in dockerfile

    entrypoint = _read(os.path.join(DOCKER, "entrypoint.sh"))
    assert "--container" in entrypoint
    assert "--data-dir=" in entrypoint
    assert "core.agent" in entrypoint
    assert "MESH_CONFIG_DIR" in entrypoint

    compose = _read(os.path.join(DOCKER, "docker-compose.yml"))
    assert "rhm-config:/data" in compose
    assert "dockerfile: docker/Dockerfile" in compose

    assert os.path.isfile(os.path.join(DOCKER, "README.md"))
    assert os.path.isfile(os.path.join(REPO, ".dockerignore"))


def test_container_assets_ship_with_the_package():
    with open(os.path.join(REPO, "package.json"), "r", encoding="utf-8") as handle:
        package = json.load(handle)
    assert "docker" in package["files"], "npm installations would lose docker/"


def _bash():
    r"""A bash that can read a Windows path: Git Bash, never WSL's System32 shim.

    C:\Windows\System32\bash.exe is the WSL launcher: it resolves the path inside
    the Linux filesystem, so "D:\...\install.sh" becomes "D:MyProjects...install.sh"
    and bash reports a file that does exist as missing.
    """
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if not candidate or not os.path.exists(candidate):
            continue
        if "system32" in candidate.replace("/", "\\").lower():
            continue
        return candidate
    return None


BASH = _bash()


@pytest.mark.skipif(BASH is None, reason="no Git Bash available")
def test_shell_installer_still_parses():
    result = subprocess.run([BASH, "-n", INSTALL_SH],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(BASH is None, reason="no Git Bash available")
def test_entrypoint_still_parses():
    result = subprocess.run([BASH, "-n", os.path.join(DOCKER, "entrypoint.sh")],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_paths_honour_the_machine_and_container_overrides(monkeypatch, tmp_path):
    """The image and the SYSTEM task both need agent.env outside "~/.config"."""
    from core import paths

    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(paths.CONFIG_DIR_ENV, str(config_dir))
    monkeypatch.setenv(paths.CACHE_DIR_ENV, str(cache_dir))
    try:
        assert paths.config_dir() == str(config_dir)
        assert paths.config_file("agent.env") == os.path.join(str(config_dir), "agent.env")
        assert paths.cache_dir() == str(cache_dir)
    finally:
        monkeypatch.delenv(paths.CONFIG_DIR_ENV, raising=False)
        monkeypatch.delenv(paths.CACHE_DIR_ENV, raising=False)

    # Unset means the historical layout, unchanged.
    default = paths.config_dir()
    assert default.startswith(paths.home_dir())
    assert default.endswith(("remote-harness-mcp", "antigravity-mesh"))


def test_blank_override_is_ignored(monkeypatch):
    from core import paths

    monkeypatch.setenv(paths.CONFIG_DIR_ENV, "   ")
    try:
        assert paths.config_dir().endswith(("remote-harness-mcp", "antigravity-mesh"))
        assert paths.config_dir().startswith(paths.home_dir())
    finally:
        monkeypatch.delenv(paths.CONFIG_DIR_ENV, raising=False)


# ---------------------------------------------------------------------------
# Administrator service variant (Windows installer and wizard)
# ---------------------------------------------------------------------------

def test_windows_installer_carries_the_service_variant():
    text = _read(INSTALL_PS1)
    assert "[switch]$AdminService" in text
    assert "Test-ElevatedSession" in text
    assert "-Verb RunAs" in text, "the variant raises UAC itself"
    assert "RemoteHarnessMCPService" in text
    assert 'New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest' in text
    assert "[switch]$Elevated" in text, "the elevated child must not elevate again"
    assert "ops\\windows\\rhm-service.ps1" in text


def test_elevation_is_requested_at_the_service_step_not_before_the_install():
    text = _read(INSTALL_PS1)
    # The UAC prompt belongs to the moment the service is registered: the node
    # part runs unelevated first, so starting the installer without admin rights
    # does not block the option.
    assert "-ServiceOnly" in text, "the elevated child must do only the service step"
    assert "if ($ServiceOnly) {" in text
    service_env_at = text.index('("MESH_CONFIG_DIR=" + $ConfigDir)')
    runas_at = text.index("Start-Process -FilePath 'powershell.exe' -Verb RunAs")
    assert service_env_at < runas_at, (
        "the machine configuration must be written before the elevation request"
    )
    assert "Узел установлен" in text, "the prompt must say the node part is already done"


def test_service_variant_uses_the_machine_wide_configuration():
    text = _read(INSTALL_PS1)
    assert 'Join-Path $ProgramDataRoot "remote-harness-mcp"' in text
    assert '("MESH_CONFIG_DIR=" + $ConfigDir)' in text


def test_service_launcher_can_host_the_standalone_server():
    text = _read(os.path.join(REPO, "ops", "windows", "rhm-service.ps1"))
    assert "MESH_SERVICE_MODE" in text, "the launcher must know what to run"
    assert "core.server" in text, "Funnel mode runs the MCP server, not the tunnel agent"
    assert "--port=$ServicePort" in text


def test_funnel_with_the_service_writes_a_server_mode_config():
    text = _read(INSTALL_PS1)
    assert "MESH_SERVICE_MODE=server" in text
    assert "Wait-StandaloneNode -Port $Port -TimeoutSec 45" in text, (
        "the service starts the server, so the installer waits for it"
    )


def test_service_launcher_pins_the_interpreter_and_restarts():
    text = _read(SERVICE)
    assert "service.env" in text
    assert "MESH_PYTHON" in text
    assert "core.agent" in text
    assert "Start-Sleep" in text, "the launcher must restart a crashed agent"
    assert all(ord(character) < 128 for character in text), "keep the launcher ASCII"


def test_dry_run_reports_the_variant_in_both_languages():
    text = _read(INSTALL_PS1)
    assert "[DRY-RUN] Mode        : $modeState" in text
    assert "[DRY-RUN] Режим       : $modeState" in text
    assert '"MCP service (SYSTEM, administrator rights)"' in text


def test_wizard_offers_the_service_as_a_switch_not_a_mode():
    text = _read(GUI)
    # The service is a parameter of the chosen variant: no separate radio button,
    # one checkbox on the options page, and the flag is appended next to the
    # variant's own arguments.
    assert "@{ Id = 'admin'" not in text, "admin must not be an install mode of its own"
    assert "opt_service_label" in text, "the options page needs the service checkbox"
    assert "$script:AdminService" in text
    assert "$argList += '-AdminService'" in text, "the wizard must pass the flag to install.ps1"
    assert "$Mode -eq 'funnel'" in text, "Funnel can host the machine service too (core.server)"


def test_service_variant_strings_are_bilingual():
    with open(STRINGS, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    for language in ("en", "ru"):
        for key in ("opt_service_group", "opt_service_label", "opt_service_hint",
                    "opt_service_unavailable", "res_service_task"):
            assert key in document[language], "%s is missing in %s" % (key, language)
    # The switch is about boot-time autostart; it is never the default, because a
    # SYSTEM task has no desktop and costs the screen tools.
    assert "службу" in document["ru"]["opt_service_label"]
    assert "старт" in document["ru"]["opt_service_label"]
    assert "администратора" in document["ru"]["opt_service_hint"]
    assert "SSH" in document["ru"]["opt_service_unavailable"]
    assert "screen capture" in document["en"]["opt_service_hint"]
    assert "скриншоты" in document["ru"]["opt_service_hint"].lower()


def test_the_wizard_defaults_to_the_user_session():
    # Screen capture, the clipboard and the tray icon need the signed-in user's
    # desktop. A SYSTEM task lives in session 0 and has none, so the machine
    # service is an explicit opt-in - never the state the wizard opens in.
    text = _read(GUI)
    assert "$script:AdminService = $false" in text
    assert "$script:AdminService = ($script:Mode -ne 'ssh')" not in text, (
        "choosing a variant must not arm the machine service behind the operator's back"
    )


def test_the_installer_says_what_the_machine_service_costs():
    # The swap is deliberate: boot-time autostart is worth it on a headless box,
    # and it silently removes three tools on a desktop. The installer has to say
    # so at install time, not leave it to a model reading "available: false".
    text = _read(INSTALL_PS1)
    assert "function Show-ServiceDesktopNote" in text
    assert text.count("Show-ServiceDesktopNote") >= 3, "both service branches must print it"
    assert "session 0" in text
    assert "нет рабочего стола" in text, "the Russian note must name the missing desktop"


def test_the_wizard_shows_the_registered_autostart_not_the_requested_one():
    text = _read(GUI)
    assert "function Get-ActualAutostart" in text
    assert "autostart.state.json" in text
    assert "Get-ActualAutostart -Service" in text


def test_wizard_reads_the_service_configuration_for_its_url_fallback():
    text = _read(GUI)
    assert "ServiceAgentEnvFile" in text
    assert "if ($script:AdminService) { $ServiceAgentEnvFile }" in text
