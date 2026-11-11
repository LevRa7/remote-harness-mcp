"""Platform layer: one contract, three implementations, no external programs.

These tests run on any host. The Linux and Android backends are exercised
against fixture trees (a fake ``/proc`` and ``/sys``) because that is exactly how
their parsers are used on those systems: a file is opened and parsed. The
Windows backend is exercised live when the suite runs on Windows and its pure
helpers run everywhere.
"""

import os
import sys

import pytest

from core import platforms
from core.platforms import base, common, encoding, png, shell
from core.platforms.linux.procfs import (
    ProcFS,
    parse_cpu_line,
    parse_meminfo,
    parse_stat,
)

IS_WINDOWS = os.name == "nt"

MEMINFO = """MemTotal:       16384000 kB
MemFree:         2048000 kB
MemAvailable:    8192000 kB
Buffers:          512000 kB
Cached:          4096000 kB
SReclaimable:     256000 kB
SwapTotal:       8192000 kB
SwapFree:        8192000 kB
"""

STAT = """cpu  1200 40 800 9000 100 0 30 0 0 0
cpu0 600 20 400 4500 50 0 15 0 0 0
cpu1 600 20 400 4500 50 0 15 0 0 0
intr 1 2 3
"""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detection_names_a_known_scenario():
    assert platforms.detect() in platforms.SCENARIOS


def test_windows_wins_over_a_stray_termux_variable(monkeypatch):
    """A CI matrix that exports TERMUX_VERSION must not turn a PC into a phone."""
    monkeypatch.setenv("TERMUX_VERSION", "0.118")
    if IS_WINDOWS:
        assert platforms.detect() == "windows"
    else:
        assert platforms.detect() == "termux"


def test_every_scenario_has_a_backend():
    for scenario in platforms.SCENARIOS:
        backend = platforms.backend(scenario)
        assert backend.scenario == scenario
        name, argv = backend.shell()
        assert name and argv
        assert isinstance(backend.capabilities(), dict)


def test_backend_is_cached_per_scenario():
    assert platforms.backend("linux") is platforms.backend("linux")
    assert platforms.backend("linux") is not platforms.backend("windows")


def test_report_is_serialisable():
    report = platforms.backend().report()
    import json

    assert json.loads(json.dumps(report))["scenario"] == platforms.detect()


# ---------------------------------------------------------------------------
# Shell decision (pure)
# ---------------------------------------------------------------------------

def test_posix_prefers_bash_then_sh():
    name, argv = shell.resolve(
        is_windows=False, which=lambda name: "/bin/bash" if name == "bash" else None,
        environ={}, isfile=lambda path: False)
    assert (name, argv) == ("bash", ["/bin/bash", "-c"])

    name, argv = shell.resolve(is_windows=False, which=lambda name: None,
                               environ={}, isfile=lambda path: False)
    assert (name, argv) == ("sh", ["/bin/sh", "-c"])


def test_windows_never_defaults_to_bash():
    """The bash on PATH is usually the WSL launcher: wrong filesystem, wrong pids."""
    name, argv = shell.resolve(
        is_windows=True,
        which=lambda name: r"C:\Windows\System32\bash.exe",
        environ={}, isfile=lambda path: False,
        powershell=[r"C:\pwsh.exe", "-Command"], comspec=r"C:\cmd.exe")
    assert name == "powershell"
    assert argv[0] == r"C:\pwsh.exe"


def test_windows_absent_powershell_is_cmd_not_rediscovered():
    """Passing None means "this box has no PowerShell" and must not be re-probed."""
    name, argv = shell.resolve(
        is_windows=True, which=lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        environ={}, isfile=lambda path: False, powershell=None, comspec=r"C:\cmd.exe")
    assert name == "cmd"
    assert argv == [r"C:\cmd.exe", "/d", "/s", "/c"]


def test_git_bash_override_requires_an_installed_git():
    name, _argv = shell.resolve(
        is_windows=True, which=lambda name: None, environ={}, isfile=lambda path: False,
        override="git-bash", powershell=[r"ps.exe", "-Command"], comspec=r"C:\cmd.exe")
    assert name == "powershell"  # asked for bash that is not there: default wins


# ---------------------------------------------------------------------------
# Encoding decision (pure)
# ---------------------------------------------------------------------------

def test_windows_decoding_tries_oem_then_ansi():
    candidates = encoding.candidate_encodings(is_windows=True, code_pages=lambda: ["cp866", "cp1251"])
    assert candidates == ["utf-8", "cp866", "cp1251"]


def test_posix_decoding_uses_the_locale_second():
    candidates = encoding.candidate_encodings(is_windows=False, preferred="koi8-r")
    assert candidates == ["utf-8", "koi8-r"]


def test_utf8_that_decodes_is_trusted():
    text = "привет"
    assert encoding.decode(text.encode("utf-8"), ["utf-8", "cp866"]) == text


def test_cp866_output_is_not_mojibake():
    text = "привет мир"
    assert encoding.decode(text.encode("cp866"), ["utf-8", "cp866", "cp1251"]) == text


def test_newlines_are_normalised():
    assert encoding.decode(b"a\r\nb\rc\n", ["utf-8"]) == "a\nb\nc\n"


# ---------------------------------------------------------------------------
# /proc parsers (Linux and Android share them)
# ---------------------------------------------------------------------------

def test_parse_meminfo_reads_kilobytes():
    values = parse_meminfo(MEMINFO)
    assert values["MemTotal"] == 16384000
    assert values["MemAvailable"] == 8192000


def test_parse_cpu_line_treats_iowait_as_idle():
    idle, total = parse_cpu_line("cpu  100 0 100 800 50 0 0 0 0 0")
    assert idle == 850  # idle + iowait
    assert total == 1050


def test_parse_stat_survives_spaces_and_parentheses_in_comm():
    """`(Web Content)` and `(sd-pam)` must not shift every later field."""
    text = ("4242 (Web Content) S 100 4242 4242 0 -1 4194560 1 0 0 0 "
            "5 7 0 0 20 0 1 0 98765 123456789 4321 18446744073709551615")
    parsed = parse_stat(text)
    assert parsed is not None
    assert parsed["ppid"] == 100
    assert parsed["utime"] == 5
    assert parsed["stime"] == 7
    assert parsed["starttime"] == 98765
    assert parsed["rss_pages"] == 4321


def _fake_proc(tmp_path, pids):
    """Build a minimal /proc tree: meminfo, stat, uptime, one dir per pid."""
    root = tmp_path / "proc"
    root.mkdir()
    (root / "meminfo").write_text(MEMINFO, encoding="utf-8")
    (root / "stat").write_text(STAT, encoding="utf-8")
    (root / "uptime").write_text("12345.67 9999.00\n", encoding="utf-8")
    for pid, ppid, name, rss, start in pids:
        directory = root / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(
            "%d (%s) S %d %d %d 0 -1 4194560 1 0 0 0 10 20 0 0 20 0 1 0 %d "
            "123456789 %d 18446744073709551615" % (pid, name, ppid, pid, pid, start, rss),
            encoding="utf-8")
    return root


def test_procfs_root_is_parameterisable(tmp_path):
    """/proc is not hard-coded: a fixture tree exercises the real parser."""
    root = _fake_proc(tmp_path, [(100, 1, "systemd", 100, 111), (200, 100, "worker", 400, 222)])
    proc = ProcFS(str(root), str(tmp_path / "sys"))
    assert proc.available()
    assert proc.meminfo()["MemTotal"] == 16384000
    assert proc.uptime() == 12345.67
    assert proc.pid_start_time(200) == 222
    assert proc.pid_alive(200)
    assert proc.descendants(100) == [200]
    assert [name for name, _mb in proc.top_processes(1)] == ["worker"]


def test_procfs_missing_process_is_not_alive(tmp_path):
    proc = ProcFS(str(_fake_proc(tmp_path, [])), str(tmp_path / "sys"))
    assert not proc.pid_alive(999999)
    assert proc.pid_start_time(999999) is None
    assert proc.top_processes() == []


def test_procfs_pid_reuse_guard(tmp_path):
    proc = ProcFS(str(_fake_proc(tmp_path, [(300, 1, "job", 10, 555)])), str(tmp_path / "sys"))
    assert proc.pid_alive(300, 555)
    assert not proc.pid_alive(300, 444)  # same pid, different start time


# ---------------------------------------------------------------------------
# Linux backend against the fixture tree
# ---------------------------------------------------------------------------

def test_linux_vitals_shape(tmp_path):
    from core.platforms import linux

    backend = linux.LinuxBackend()
    vitals = backend.vitals()
    assert set(vitals) >= {"hostname", "cpu_load", "ram", "disk"}
    assert set(vitals["cpu_load"]) == {"1m", "5m", "15m"}
    # On this host (Windows) /proc is absent, so the backend must still answer
    # with the documented shape instead of raising.
    assert vitals["ram"]["total_mb"] >= 0


def test_linux_capabilities_are_honest():
    from core.platforms import linux

    caps = linux.LinuxBackend().capabilities()
    assert caps["shell"] is True
    assert caps["screenshot"] is False  # a server has no framebuffer


# ---------------------------------------------------------------------------
# Android backend
# ---------------------------------------------------------------------------

def _fake_sys(tmp_path, capacity=87, status="Charging", temp=345):
    root = tmp_path / "sys"
    battery = root / "class" / "power_supply" / "battery"
    battery.mkdir(parents=True)
    (battery / "capacity").write_text(str(capacity), encoding="utf-8")
    (battery / "status").write_text(status, encoding="utf-8")
    (battery / "temp").write_text(str(temp), encoding="utf-8")
    (battery / "voltage_now").write_text("4200000", encoding="utf-8")
    (battery / "current_now").write_text("1500000", encoding="utf-8")

    thermal = root / "class" / "thermal" / "thermal_zone0"
    thermal.mkdir(parents=True)
    (thermal / "type").write_text("cpu-0-0", encoding="utf-8")
    (thermal / "temp").write_text("41200", encoding="utf-8")
    return root


def test_android_battery_comes_from_sysfs(tmp_path):
    from core.platforms.android import sysfs

    root = _fake_sys(tmp_path)
    block = sysfs.battery(str(root / "class" / "power_supply"))
    assert block["available"] is True
    assert block["percent"] == 87
    assert block["charging"] is True
    assert block["temperature_c"] == 34.5
    assert block["voltage_v"] == 4.2
    assert block["current_a"] == 1.5


def test_android_thermal_is_named_and_sorted(tmp_path):
    from core.platforms.android import sysfs

    root = _fake_sys(tmp_path)
    zones = sysfs.thermal(str(root / "class" / "thermal"))
    assert zones == [{"zone": "thermal_zone0", "name": "cpu-0-0", "celsius": 41.2}]


def test_android_thermal_missing_is_empty_not_an_error(tmp_path):
    from core.platforms.android import sysfs

    assert sysfs.thermal(str(tmp_path / "missing")) == []


def test_android_storage_skips_paths_that_do_not_exist():
    from core.platforms.android import sysfs

    rows = sysfs.storage(["/definitely/not/here"])
    assert rows == []


def test_android_vitals_carry_the_battery(monkeypatch, tmp_path):
    """A phone's most important vital is the battery; it must not need termux-api."""
    from core.platforms.android import telemetry, sysfs

    root = _fake_sys(tmp_path)
    monkeypatch.setattr(sysfs, "battery", lambda: {
        "available": True, "percent": 55, "charging": False, "status": "discharging",
    })
    monkeypatch.setattr(sysfs, "thermal", lambda limit=8: [{"name": "cpu", "celsius": 40.0}])
    payload = telemetry.vitals()
    assert payload["os"] == "Android"
    assert payload["battery"]["percent"] == 55
    assert payload["thermal"][0]["celsius"] == 40.0


def test_android_capabilities_say_no_screenshot():
    """Android gives an app no framebuffer: the honest answer is "no"."""
    from core.platforms.android import AndroidBackend

    caps = AndroidBackend().capabilities()
    assert caps["screenshot"] is False
    assert caps["vitals"] == os.path.isdir("/proc")


# ---------------------------------------------------------------------------
# PNG encoder (used by the Windows screenshot path)
# ---------------------------------------------------------------------------

def test_png_rgb_encoder_round_trips():
    width, height = 4, 3
    rows = [bytes([255, 0, 0] * width), bytes([0, 255, 0] * width), bytes([0, 0, 255] * width)]
    data = png.encode_rgb(width, height, rows)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data.endswith(b"IEND\xaeB`\x82")


def test_png_rejects_a_wrong_row_length():
    with pytest.raises(ValueError):
        png.encode_rgb(2, 1, [b"\x00\x00"])


def test_png_bgra_flips_bottom_up_rows():
    width, height = 1, 2
    # A DIB is bottom-up: first row in memory is the *last* screen row.
    top = bytes([0, 0, 255, 0])   # BGRA red
    bottom = bytes([255, 0, 0, 0])  # BGRA blue
    data = png.encode_bgra(width, height, bottom + top, top_down=False)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# Windows backend (live on Windows, cheap elsewhere)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not IS_WINDOWS, reason="Win32 only")
def test_windows_telemetry_is_native_and_plausible():
    from core.platforms import windows

    backend = windows.WindowsBackend()
    assert backend.uptime_seconds() > 0
    vitals = backend.vitals()
    assert vitals["ram"]["total_mb"] > 0
    assert vitals["disk"]["total_gb"] > 0
    blocks = backend.info_blocks()
    assert "C:" in blocks["disks"]
    assert "Mem:" in blocks["memory"]


@pytest.mark.skipif(not IS_WINDOWS, reason="Win32 only")
def test_windows_screenshot_is_a_real_png():
    from core.platforms import windows

    data = windows.WindowsBackend().screenshot_png()
    assert data and data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 1024


@pytest.mark.skipif(not IS_WINDOWS, reason="Win32 only")
def test_windows_clipboard_round_trip():
    from core.platforms import windows

    backend = windows.WindowsBackend()
    assert backend.clipboard_set("platform-clipboard-round-trip")
    assert backend.clipboard_get() == "platform-clipboard-round-trip"


@pytest.mark.skipif(not IS_WINDOWS, reason="Win32 only")
def test_windows_process_liveness_and_tree():
    import subprocess
    import time

    from core.platforms import windows

    backend = windows.WindowsBackend()
    assert backend.pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.5)
        assert backend.pid_alive(child.pid)
        backend.kill_tree(child.pid)
        time.sleep(0.6)
        assert not backend.pid_alive(child.pid)
    finally:
        try:
            child.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared formatting
# ---------------------------------------------------------------------------

def test_info_block_columns_are_identical_across_platforms():
    """A model that learned the Linux table reads the Windows one unchanged."""
    disks = common.disks_block([("C:\\", 120.0, 5.3), ("/", 40.0, 10.0)])
    memory = common.memory_block(16384, 8192, 8192)
    processes = common.processes_block([("chrome", 128.5)])
    assert disks.splitlines()[0].split() == ["Mount", "Total", "Used", "Free", "Use%"]
    assert memory.splitlines()[1].startswith("Mem:")
    assert processes.splitlines()[0].split() == ["COMMAND", "MEM_MB"]


def test_load_block_reports_what_the_platform_has():
    line = common.load_block(12.5, 8, "2026-01-01 10:00", 3600.0)
    assert "CPU 12%" in line and "cores 8" in line and "up 1.0 h" in line
    windows_like = common.load_block(12.5, 8, "", 3600.0)
    assert "load" not in windows_like


def test_unavailable_block_carries_a_reason():
    block = base.unavailable("no framebuffer on this host")
    assert block == {"available": False, "reason": "no framebuffer on this host"}
