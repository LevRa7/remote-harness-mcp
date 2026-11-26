"""The Windows installer must ask where the node goes, and what it sets up.

Until now the wizard unpacked the payload into a versioned scratch folder under
%LOCALAPPDATA% and lived there: the console shim, the autostart entry and the
updater all named that folder, so installing the node anywhere else was not an
option. The location page plus install.ps1's -InstallDir, -Autostart, -AddToPath
and -Shortcuts are what make it one. These tests pin the wiring; the only piece
of behaviour that can be executed without touching this machine is the payload
copy, so that one is extracted from install.ps1 and run on a temporary tree.
"""

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_SCRIPT = os.path.join(REPO, "install-gui.ps1")
GUI_STRINGS = os.path.join(REPO, "install-gui.strings.json")
INSTALL_PS1 = os.path.join(REPO, "install.ps1")

needs_windows = pytest.mark.skipif(
    os.name != "nt", reason="the installer and the wizard are Windows-only"
)


def _text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _strings():
    return json.loads(_text(GUI_STRINGS))


def _powershell():
    candidate = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32",
        "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    return candidate if os.path.exists(candidate) else "powershell.exe"


def _function_source(text, name):
    """The body of one top-level function, up to the next one."""
    start = text.index("function %s {" % name)
    end = text.find("\nfunction ", start + 1)
    return text[start:end if end != -1 else len(text)]


def test_the_wizard_asks_where_the_node_goes():
    gui = _text(GUI_SCRIPT)
    assert "function Build-LocationPage {" in gui
    assert "function Get-InstallDrives {" in gui
    assert "FolderBrowserDialog" in gui, "the folder can be picked, not only typed"
    assert "Build-LocationPage" in gui.split("switch ($Index) {")[1], (
        "the location page must be part of the page switch")

    strings = _strings()
    for key in ("page_location_title", "loc_group", "loc_intro", "loc_drive_label",
                "loc_path_label", "loc_browse", "loc_free", "loc_hint", "loc_err_path",
                "loc_err_root", "loc_err_system", "loc_err_drive", "loc_err_space",
                "loc_err_create"):
        assert key in strings["en"], key
        assert key in strings["ru"], key


def test_the_options_page_offers_autostart_path_and_shortcuts():
    gui = _text(GUI_SCRIPT)
    strings = _strings()
    for key in ("opt_setup_group", "opt_autostart_label", "opt_autostart_hint",
                "opt_path_label", "opt_path_hint", "opt_shortcuts_label",
                "opt_shortcuts_hint"):
        assert key in strings["en"], key
        assert key in strings["ru"], key

    # The wizard has to hand all four switches to install.ps1 - a checkbox that
    # only changes a variable on the page is how an option quietly does nothing.
    steps = _function_source(gui, "New-ConsoleInstallerSteps")
    assert "'-InstallDir'" in steps
    assert "'-Autostart', $autostartChoice" in steps
    assert "'-AddToPath:$true'" in steps and "'-AddToPath:$false'" in steps
    assert "'-Shortcuts'" in steps
    assert "$autostartChoice = 'none'" in steps, "clearing the box must mean none"


def test_the_installer_accepts_location_and_setup_switches():
    text = _text(INSTALL_PS1)
    assert "[string]$InstallDir" in text
    assert "[ValidateSet('auto', 'service', 'logon', 'none')]" in text
    assert "[object]$AddToPath = $true" in text
    assert "[switch]$Shortcuts" in text
    assert "function Copy-NodePayload {" in text
    assert "function Get-ExistingInstallDir {" in text
    assert "function Stop-ExistingNodeInstances {" in text
    assert "Stop-ExistingNodeInstances -Language $Lang" in text
    assert "$copyError = Copy-NodePayload -Source $currentDir -Target $targetDir" in text
    # A dry run reports and changes nothing, so it must not copy a payload into a
    # folder the operator has not approved yet.
    assert "if ($InstallDir -and $ScriptDir -and -not $DryRun) {" in text


@needs_windows
@pytest.mark.parametrize("flag", ["-AddToPath:$true", "-AddToPath:$false"])
def test_powershell_51_file_invocation_accepts_addtopath_flag(flag):
    """Windows PowerShell 5.1 -File passes '-AddToPath:$true' as literal '$true'."""
    result = subprocess.run(
        [
            _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", INSTALL_PS1,
            "-Lang", "en", "-DryRun", "-Mode", "standalone", "-Funnel",
            "-InstallDir", r"D:\RemoteHarnessMCP", "-Autostart", "logon",
            flag, "-Shortcuts",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "ParameterArgumentTransformationError" not in (result.stderr or "")


def test_gui_default_dir_never_uses_version_scratch_leaf():
    gui = _text(GUI_SCRIPT)
    assert "function Get-ExistingInstallDir {" in gui
    assert "function Get-DefaultInstallDir {" in gui
    assert r"^\d+\.\d+\.\d+" in gui
    strings = _strings()
    assert "loc_existing_hint" in strings["en"]
    assert "loc_existing_hint" in strings["ru"]


def test_autostart_none_registers_no_entry_and_removes_an_old_one():
    text = _text(INSTALL_PS1)
    assert "$AutostartNone = ($Autostart -eq 'none')" in text
    assert "if ($Autostart -eq 'logon' -or $Autostart -eq 'none') { $AdminService = $false }" in text
    assert "if ($AutostartNone) {" in text
    block = text[text.index("if ($AutostartNone) {"):]
    block = block[:block.index("} else {")]
    assert "$autostartKind = \"none\"" in block
    assert "rhm-node.vbs" in block and "Remove-Item" in block
    assert "core.server" in block, "the node still runs for this session"


def test_path_is_left_alone_unless_the_operator_asks():
    text = _text(INSTALL_PS1)
    command = _function_source(text, "Install-RhmCommand")
    assert "if ($AddToPath) {" in command
    assert "PATH was left alone" in command and "PATH не изменён" in command


def test_shortcuts_go_to_the_start_menu_and_the_desktop():
    text = _text(INSTALL_PS1)
    body = _function_source(text, "Install-RhmShortcuts")
    assert "CreateShortcut" in body and "WScript.Shell" in body
    assert "Remote Harness MCP" in body
    assert "GetFolderPath(\"Programs\")" in body
    assert "GetFolderPath(\"Desktop\")" in body
    assert "if ($Shortcuts) {" in text
    assert "Install-RhmShortcuts -BinDir $binDir -PayloadDir $PayloadDir" in text


def test_the_funnel_step_checks_the_tailscale_backend_first():
    """A serve config cannot exist while the backend is not Running."""
    text = _text("install.ps1")
    block = text[text.index("if ($FunnelName) {"):]
    block = block[:block.index("$funnelOk = $false")]
    assert "Get-TailscaleState -Tailscale $TsExe" in block
    assert "Connect-Tailscale -Tailscale $TsExe" in block, (
        "a dead backend has to be started before the funnel is applied")
    assert "tailscale up" in block


def test_the_installer_keeps_the_utf8_bom_powershell_51_needs():
    with open(INSTALL_PS1, "rb") as handle:
        data = handle.read(3)
    assert data == b"\xef\xbb\xbf", "install.ps1 lost its UTF-8 BOM"


@needs_windows
def test_a_chosen_folder_gets_a_real_copy_of_the_payload(tmp_path):
    """The copy is the one part of -InstallDir that can be run for real."""
    source = tmp_path / "payload"
    (source / "core").mkdir(parents=True)
    (source / "core" / "menu.py").write_text("x = 1\n", encoding="utf-8")
    (source / "VERSION").write_text("0.0.0\n", encoding="ascii")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "menu.cpython-313.pyc").write_bytes(b"\x00")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("temporary", encoding="ascii")
    target = tmp_path / "chosen" / "node"

    harness = tmp_path / "harness.ps1"
    harness.write_text(
        _function_source(_text(INSTALL_PS1), "Copy-NodePayload")
        + "\n$problem = Copy-NodePayload -Source '%s' -Target '%s'\n" % (source, target)
        + "if ($problem) { Write-Output (\"ERR \" + $problem); exit 1 }\n",
        encoding="utf-8-sig")

    result = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert (target / "core" / "menu.py").is_file()
    assert (target / "VERSION").is_file()
    assert not (target / "__pycache__").exists(), "byte code from another interpreter travelled"
    assert not (target / ".git").exists(), "a checkout is not an installation"
