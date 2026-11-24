"""Funnel is the default install path on every platform (TSF-06).

The wizard, the console installers and the shell installer must all end up with
the same three steps: install the Tailscale client, walk the operator through
the one-time sign-in, publish the loopback node through Funnel. These are
source-level assertions on purpose: the real flow needs a tailnet and a browser,
which a unit test cannot provide, but the wiring is exactly what breaks.
"""

import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _text(name):
    with open(os.path.join(REPO, name), "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# install.ps1: the authorization prompt happens right after installation
# ---------------------------------------------------------------------------


def test_windows_installer_installs_tailscale_when_missing():
    text = _text("install.ps1")
    assert "function Install-Tailscale" in text
    assert "winget.exe install" in text
    assert "Tailscale.Tailscale" in text


def test_windows_installer_waits_for_the_sign_in_and_shows_the_url():
    text = _text("install.ps1")
    assert "function Connect-Tailscale" in text
    assert r"https://login\.tailscale\.com/a/" in text
    assert "BackendState" in text, "the wait must be driven by the real backend state"


def test_windows_installer_explains_the_funnel_acl_requirement():
    text = _text("install.ps1")
    assert "function Show-FunnelAclHint" in text
    assert "nodeAttrs" in text
    assert "admin/acls" in text


# ---------------------------------------------------------------------------
# The wizard: Funnel is the default variant
# ---------------------------------------------------------------------------


def test_gui_declares_funnel_and_selects_it_first():
    text = _text("install-gui.ps1")
    assert re.search(r"ValidateSet\([^)]*'funnel'[^)]*\)", text)
    assert re.search(r"ValidateSet\([^)]*'admin'[^)]*\)", text), "the admin variant must stay selectable"
    assert "Id = 'funnel'" in text
    assert "$script:Mode = 'funnel'" in text
    funnel_at = text.index("Id = 'funnel'")
    quick_at = text.index("Id = 'quick'")
    assert funnel_at < quick_at


def test_gui_maps_funnel_to_standalone_plus_the_flag():
    text = _text("install-gui.ps1")
    assert re.search(r"\$argList \+= @\('-Mode', 'standalone', '-Funnel'\)", text)


def test_gui_strings_define_the_funnel_mode_in_both_languages():
    with open(os.path.join(REPO, "install-gui.strings.json"), "r", encoding="utf-8") as handle:
        data = json.load(handle)
    for lang in ("en", "ru"):
        for key in ("mode_funnel_title", "mode_funnel_desc", "opt_funnel_group", "opt_funnel_note", "res_funnel_note"):
            assert data[lang].get(key), f"{lang}.{key} is missing"


# ---------------------------------------------------------------------------
# install.sh: the same flow for Linux and macOS
# ---------------------------------------------------------------------------


def test_shell_installer_accepts_the_funnel_mode():
    text = _text("install.sh")
    assert "--mode=tunnel|standalone|funnel|gateway" in text
    assert re.search(r"if \[ \"\$MODE\" = \"funnel\" \]; then", text)
    assert 'MODE="standalone"' in text


def test_shell_installer_installs_and_connects_tailscale():
    text = _text("install.sh")
    assert "find_tailscale()" in text
    assert "https://tailscale.com/install.sh" in text
    assert "connect_tailscale()" in text
    assert r"login\.tailscale\.com/a/" in text


def test_shell_installer_writes_the_allowlist_and_starts_funnel():
    text = _text("install.sh")
    assert "MESH_ALLOWED_HOSTS" in text
    assert "start_funnel()" in text
    assert "funnel --bg --https=443" in text


def test_shell_installer_says_termux_has_no_funnel():
    text = _text("install.sh")
    assert "Termux has no tailscaled" in text

def test_windows_installer_restarts_a_stale_node_and_waits_for_health():
    text = _text("install.ps1")
    assert "function Stop-StandaloneNode" in text, (
        "an old node on the port answers with its own environment and returns 403"
    )
    assert "function Wait-StandaloneNode" in text
    assert "Stop-StandaloneNode -Port $Port" in text


def test_windows_autostart_needs_no_elevation():
    text = _text("install.ps1")
    assert "function Register-StandaloneStartup" in text
    assert "rhm-node.vbs" in text
    assert "[System.IO.File]::WriteAllLines" in text, (
        "Set-Content joined the launcher lines into one broken VBScript statement"
    )


def test_windows_installer_keeps_the_existing_token():
    text = _text("install.ps1")
    assert "a reinstall must not silently" in text
    assert "MESH_TOKEN=(.+)$" in text


def test_funnel_is_the_default_mode_on_every_desktop_platform():
    windows = _text("install.ps1")
    assert '[string]$Mode = "funnel"' in windows
    assert 'if ($Mode -eq "funnel") {' in windows, "the mode must normalise to standalone+Funnel"
    shell = _text("install.sh")
    # The menu puts Funnel first, and a non-interactive run defaults to it.
    assert shell.index("Публичная ссылка через Tailscale Funnel") < shell.index("Быстрая настройка")
    assert 'MODE="standalone"' in shell
    assert 'if [ "$IS_TERMUX" = true ]; then' in shell, (
        "Termux has no tailscaled: a phone must keep the gateway tunnel"
    )


def test_standalone_does_not_require_a_gateway_domain():
    text = _text("install.ps1")
    assert "$DomainMissing = $false" in text, (
        "Funnel/standalone never talks to a gateway, so a missing domain is not fatal"
    )
    shell = _text("install.sh")
    # The default is applied BEFORE the domain gate, otherwise a fresh machine
    # stops with "no gateway domain" on a mode that does not need one.
    assert 'if [ -z "$MODE" ] && { [ "$QUICK" = true ] || [ ! -t 0 ]; }' in shell, (
        "a non-interactive run must pick a mode before the gateway checks"
    )

def test_sudo_password_is_requested_explicitly_not_by_restarting_under_sudo():
    text = _text("install.sh")
    assert "read -rs SUDO_PASS" in text, "the password must be asked for, visibly"
    assert "sudo -S -v" in text
    assert "Run: ${CYAN}sudo bash install.sh" not in text, (
        "telling the operator to restart under sudo is the behaviour this replaced"
    )


def test_funnel_activation_link_waits_and_retries():
    text = _text("install.sh")
    assert "Enable Funnel at the link above" in text
    assert "funnel_attempt" in text, "the installer must retry after the operator enables Funnel"
    assert 'timeout 8' in text, "a hanging funnel call must not block the install"


def test_windows_installer_refuses_a_foreign_port_owner():
    text = _text("install.ps1")
    assert "is held by $portOwner" in text


def test_the_node_is_started_through_its_autostart_entry_not_as_an_installer_child():
    """A child of the installer died with it: "installed" node gone minutes later."""
    text = _text("install.ps1")
    assert "Register-StandaloneStartup -Python $pyExeOnly" in text
    assert "wscript.exe" in text, "the node must start detached through the Startup launcher"
    assert "$nodeProc.HasExited" not in text, "a direct child cannot outlive the install"


def test_the_installer_records_which_autostart_it_really_registered():
    text = _text("install.ps1")
    assert "autostart.state.json" in text
    assert "kind = $autostartKind" in text
    assert '$autostartKind = "service"' in text
    assert '$autostartKind = "logon"' in text


def test_a_replaced_service_task_restarts_its_running_instance():
    """Re-registering a task does not restart it; the 502 came from the old mode."""
    text = _text("install.ps1")
    service = text[text.index("function Register-McpServiceTask"):]
    service = service[:service.index("function Remove-PerUserNodeAutostart")]
    assert "Stop-ScheduledTask -TaskName $taskName" in service
    assert "Stop-Process" in service, "Stop-ScheduledTask leaves the python child alive"
    assert "Start-ScheduledTask -TaskName $taskName" in service


def test_installing_the_service_removes_a_per_user_autostart():
    text = _text("install.ps1")
    assert "function Remove-PerUserNodeAutostart" in text
    assert "Unregister-ScheduledTask -TaskName \"RemoteHarnessMCPNode\"" in text
    assert "rhm-node.vbs" in text


def test_the_windows_installer_ships_the_rhm_command():
    text = _text("install.ps1")
    assert "function Install-RhmCommand" in text
    assert "rhm.cmd" in text
    assert "-m core.menu" in text
    assert 'SetEnvironmentVariable("Path", $newPath, "User")' in text



# ---------------------------------------------------------------------------
# The `rhm` command must not carry the payload path
# ---------------------------------------------------------------------------

def test_the_windows_rhm_command_reads_its_payload_from_a_file():
    """A payload that moved used to leave the shim pointing at the old folder.

    The failure was silent: cmd printed "No module named core.menu", which names
    neither the stale path nor the file to fix. The path now lives in rhm.env,
    which every install rewrites.
    """
    text = _text("install.ps1")
    assert "rhm.env" in text
    assert '("RHM_PAYLOAD=" + $PayloadDir)' in text
    assert '("RHM_PYTHON=" + $Python)' in text
    assert 'for /f "usebackq tokens=1,* delims=="' in text, "the shim parses rhm.env"
    assert 'pushd "%RHM_PAYLOAD%"' in text
    assert "rhm_missing" in text, "a stale path needs its own message, not a Python traceback"
    assert 'pushd "' + "$PayloadDir" not in text, "the old shim baked the path in"


def test_the_shell_rhm_command_reads_its_payload_from_launcher_env():
    """The POSIX shim did not even change into the payload: `rhm` from another
    directory failed no matter how correct the path was."""
    text = _text("install.sh")
    assert "launcher.env" in text
    assert "RHM_PAYLOAD='%s'" in text
    assert 'cd "$RHM_PAYLOAD" || exit 1' in text
    assert 'exec "${RHM_PYTHON:-python3}" -m core.menu "$@"' in text
    assert "exit 127" in text, "a missing payload is an error, not an empty menu"

def test_a_wsl_install_does_not_take_the_windows_port():
    """WSL2 forwards Windows localhost: the Windows node owns 8096 there."""
    text = _text("install.sh")
    assert "PORT_EXPLICIT=true" in text
    assert "[ \"$PORT_EXPLICIT\" != true ] && grep -q -i \"microsoft\" /proc/version" in text
    assert 'PORT="8097"' in text


def test_the_shell_installer_puts_rhm_on_the_login_path():
    text = _text("install.sh")
    launcher = text[text.index("install_rhm_launcher()"):]
    launcher = launcher[:launcher.index("start_tailscaled()")]
    assert "remote-harness-mcp: rhm" in launcher, "the PATH line must be idempotent"
    assert '"$HOME/.profile"' in launcher and '"$HOME/.bashrc"' in launcher
    assert "ln -sf \"$bindir/rhm\" /usr/local/bin/rhm" in launcher


def test_a_distro_without_systemd_gets_a_login_autostart():
    text = _text("install.sh")
    assert "install_profile_autostart()" in text
    assert "elif ! systemctl --user show-environment" in text
    hook = text[text.index("install_profile_autostart()"):]
    hook = hook[:hook.index("if [ \"$MODE\" = \"standalone\" ]")]
    assert "rhm-node-autostart.sh" in hook
    assert "nohup" in hook, "the hook must actually start the server"
    assert "rhm-node-autostart" in hook, "the profile hook must be installable once"
    assert "curl -fsS --max-time 2" in hook, "an already-running server must not be duplicated"

def test_a_busy_default_port_does_not_fail_the_windows_install():
    text = _text("install.ps1")
    assert "function Resolve-FreePort" in text
    assert "$PortExplicit = $PSBoundParameters.ContainsKey('Port')" in text
    assert "$chosenPort = Resolve-FreePort -Start $Port" in text
    assert "$Port = $chosenPort" in text
    assert "if ($portOwner -and $PortExplicit)" in text, "an explicit port is never overridden"
    assert "укажите другой -Port" in text


def test_a_busy_default_port_does_not_fail_the_shell_install():
    text = _text("install.sh")
    assert "first_free_port()" in text
    assert 'FREE_PORT=$(first_free_port "$PORT")' in text
    assert 'PORT="$FREE_PORT"' in text
    assert 'if [ "$PORT_EXPLICIT" != true ]' in text
