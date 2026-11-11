"""The Windows tray script must at least build its menu headlessly.

A tray that only exists as a file is not a feature: this test runs the script's
``-SelfTest`` mode (no window, no icon) and checks the menu inventory.  The
runtime launch is covered manually (artifacts/TRY-01/runtime.log).
"""

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAY = os.path.join(REPO, "ops", "windows", "rhm-tray.ps1")


@pytest.mark.skipif(os.name != "nt", reason="Windows tray")
def test_tray_selftest_reports_menu_items():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", TRAY, "-SelfTest"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["items"] == [
        "status", "pause", "readonly", "usage", "config", "autostart", "restart", "exit"
    ]
    assert "token" not in payload
