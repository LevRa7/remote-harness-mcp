"""core/platforms/linux - the Linux (and generic POSIX) backend.

Linux is the platform the node was written on, so it is the reference for what
"the full set of tools" means.  This backend keeps that behaviour but removes
its last external dependencies: telemetry reads ``/proc`` instead of running
``df``/``free``/``ps``/``uptime``, and process control is a syscall instead of
``ps``/``kill``.

``DarwinBackend`` lives here too: macOS is POSIX with the same shell and the same
``os`` module, and the parts that differ (memory counters) are two ``sysctl``
calls the OS ships with.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.platforms import common, encoding, shell
from core.platforms.base import PlatformBackend
from core.platforms.linux import process as _process
from core.platforms.linux import telemetry as _telemetry
from core.platforms.linux.procfs import ProcFS


class LinuxBackend(PlatformBackend):
    scenario = "linux"
    label = "Linux"

    def __init__(self) -> None:
        self._shell_cache: Optional[Tuple[str, List[str]]] = None
        self._encodings: Optional[List[str]] = None

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        if self._shell_cache is None:
            self._shell_cache = shell.resolve(
                is_windows=False, which=shutil.which, environ=os.environ,
                isfile=os.path.isfile)
        return self._shell_cache

    def child_env(self) -> Dict[str, str]:
        return dict(os.environ)

    def output_encodings(self) -> List[str]:
        if self._encodings is None:
            self._encodings = encoding.candidate_encodings(is_windows=False)
        return self._encodings

    # -- telemetry ---------------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        return ProcFS().uptime()

    def vitals(self) -> Dict[str, object]:
        return _telemetry.vitals()

    def info_blocks(self) -> Dict[str, str]:
        return _telemetry.info_blocks()

    def wallpaper(self) -> str:
        """KDE and GNOME store the current wallpaper in a file, not a syscall."""
        kde = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")
        if os.path.isfile(kde):
            for line in common.read_text(kde, 262144).splitlines():
                if line.startswith("Image="):
                    return line.strip()[len("Image="):].replace("file://", "")
        gsettings = shutil.which("gsettings")
        if gsettings:
            _out, _err, code, _dur = common.run_argv(
                [gsettings, "get", "org.gnome.desktop.background", "picture-uri"], timeout=3.0)
            if code == 0 and "file://" in _out:
                return _out.split("file://", 1)[1].strip().strip("'\"")
        return ""

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        return _process.alive(pid, start_time)

    def pid_start_time(self, pid: int) -> Optional[int]:
        return _process.start_time_of(pid)

    def kill_tree(self, pid: int) -> None:
        _process.kill_tree(pid)

    def capabilities(self) -> Dict[str, bool]:
        return {
            "shell": True,
            "vitals": os.path.isdir("/proc"),
            "info_blocks": os.path.isdir("/proc"),
            "process_tree": os.path.isdir("/proc"),
            # A server has no framebuffer to capture; saying so is the honest
            # answer, and the screenshot tool reports it instead of a black PNG.
            "screenshot": False,
            "clipboard": False,
        }


class DarwinBackend(LinuxBackend):
    """macOS: same shell and ``os`` module, different memory counters.

    ``sysctl`` and ``vm_stat`` ship with the OS, so using them is not the
    "separately installed program" problem the rest of this package avoids - and
    there is no stdlib call that returns macOS free memory.
    """

    scenario = "darwin"
    label = "macOS"

    def vitals(self) -> Dict[str, object]:
        return _darwin_vitals()

    def info_blocks(self) -> Dict[str, str]:
        vitals = _darwin_vitals()
        usage = _darwin_memory()
        return {
            "disks": common.disks_block([("/", vitals["disk"]["total_gb"],
                                          vitals["disk"]["free_gb"])]),
            "memory": common.memory_block(usage["total_mb"], usage["used_mb"], usage["free_mb"]),
            "load": common.load_block(None, common.cores(), "", self.uptime_seconds(),
                                      common.load_average()),
            "top_processes": common.processes_block(_darwin_top_processes()),
        }

    def capabilities(self) -> Dict[str, bool]:
        return {
            "shell": True,
            "vitals": True,
            "info_blocks": True,
            "process_tree": False,  # no /proc; a real port would use libproc
            "screenshot": False,
            "clipboard": False,
        }


def _darwin_memory() -> Dict[str, float]:
    """Total/free RAM from ``sysctl`` and ``vm_stat`` (the only honest source)."""
    total_mb = free_mb = 0.0
    _out, _err, code, _dur = common.run_argv(["sysctl", "-n", "hw.memsize"], timeout=3.0)
    if code == 0 and _out.strip().isdigit():
        total_mb = round(int(_out.strip()) / common.MB, 1)
    _out, _err, code, _dur = common.run_argv(["vm_stat"], timeout=3.0)
    if code == 0:
        page = 4096
        free_pages = 0
        for line in _out.splitlines():
            if "page size of" in line:
                try:
                    page = int(line.split()[-2])
                except (IndexError, ValueError):
                    page = 4096
            elif "Pages free:" in line or "Pages speculative:" in line:
                try:
                    free_pages += int(line.split()[-1].rstrip("."))
                except (IndexError, ValueError):
                    continue
        free_mb = round(free_pages * page / common.MB, 1)
    return {"total_mb": total_mb, "avail_mb": free_mb,
            "used_mb": round(max(0.0, total_mb - free_mb), 1)}


def _darwin_vitals() -> Dict[str, object]:
    usage = _darwin_memory()
    load = common.load_average() or (0.0, 0.0, 0.0)
    disk = common.disk_usage("/") or (0.0, 0.0, 0.0)
    total_gb, used_gb, free_gb = disk
    return {
        "hostname": common.hostname(),
        "os": "Darwin",
        "cpu_load": {"1m": float(load[0]), "5m": float(load[1]), "15m": float(load[2])},
        "ram": {
            "total_mb": usage["total_mb"],
            "used_mb": usage["used_mb"],
            "free_mb": usage["avail_mb"],
            "used_pct": common.pct(usage["used_mb"], usage["total_mb"]),
        },
        "disk": {
            "total_gb": total_gb, "used_gb": used_gb, "free_gb": free_gb,
            "used_pct": common.pct(used_gb, total_gb),
        },
    }


def _darwin_top_processes(limit: int = 5):
    _out, _err, code, _dur = common.run_argv(
        ["ps", "-Ao", "comm=,%mem=", "-r"], timeout=3.0)
    rows = []
    if code == 0:
        for line in _out.splitlines()[:limit]:
            parts = line.rsplit(None, 1)
            if len(parts) == 2:
                try:
                    rows.append((parts[0].strip()[-26:], float(parts[1])))
                except ValueError:
                    continue
    return rows


__all__ = ["DarwinBackend", "LinuxBackend"]
