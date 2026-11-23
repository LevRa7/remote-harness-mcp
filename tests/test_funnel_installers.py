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
