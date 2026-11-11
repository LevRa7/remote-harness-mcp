"""core/platforms/windows/telemetry.py - host metrics straight from Win32.

Every number here comes from a kernel call, so ``system_info`` and
``system_vitals`` answer on a *clean* Windows box with no PowerShell, no WMI
service, no ``wmic`` and no Git - and they answer in milliseconds instead of the
one-to-three seconds a CIM query costs.

What replaces what:

============================  ==============================  ==================
value                         old source                      here
============================  ==============================  ==================
total/available RAM           ``Get-CimInstance Win32_OS``    ``GlobalMemoryStatusEx``
CPU utilisation               ``Win32_Processor`` CIM         ``GetSystemTimes`` delta
uptime                        ``LastBootUpTime``              ``GetTickCount64``
disks and free space          ``Win32_LogicalDisk`` CIM       ``GetDiskFreeSpaceExW``
top processes by memory       ``Get-Process``                 ``Toolhelp32`` + ``GetProcessMemoryInfo``
============================  ==============================  ==================

The CPU figure is a short delta rather than an instantaneous counter: Windows
keeps a load *percentage* per core, not a load average, so the honest equivalent
is "how much of the last 100 ms was not idle".
"""

from __future__ import annotations

import ctypes
import time
from typing import Any, Dict, List, Optional, Tuple

from core.platforms import common
from core.platforms.windows import winapi

#: How long the CPU sample covers. Short enough for a tool call, long enough that
#: a single scheduler tick cannot make the answer 0% or 100%.
CPU_SAMPLE_SECONDS = 0.09


def _check_handle(handle) -> bool:
    return bool(handle) and handle != winapi.INVALID_HANDLE_VALUE


def memory() -> Dict[str, float]:
    """``{total_mb, avail_mb, used_mb, load_pct}`` from ``GlobalMemoryStatusEx``."""
    empty = {"total_mb": 0.0, "avail_mb": 0.0, "used_mb": 0.0, "load_pct": 0.0}
    if winapi.GlobalMemoryStatusEx is None:
        return empty
    status = winapi.MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(winapi.MEMORYSTATUSEX)
    if not winapi.GlobalMemoryStatusEx(ctypes.byref(status)):
        return empty
    total = status.ullTotalPhys / common.MB
    available = status.ullAvailPhys / common.MB
    return {
        "total_mb": round(total, 1),
        "avail_mb": round(available, 1),
        "used_mb": round(max(0.0, total - available), 1),
        "load_pct": round(float(status.dwMemoryLoad), 1),
    }


def cpu_times() -> Optional[Tuple[int, int, int]]:
    """``(idle, kernel, user)`` in 100 ns units, or ``None`` when unavailable.

    ``kernel`` time already includes ``idle`` on Windows, which is the classic
    off-by-a-lot in hand-written CPU calculations; :func:`cpu_percent` accounts
    for it.
    """
    if winapi.GetSystemTimes is None:
        return None
    idle, kernel, user = winapi.FILETIME(), winapi.FILETIME(), winapi.FILETIME()
    if not winapi.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return (idle.value(), kernel.value(), user.value())


def cpu_percent(sample_seconds: float = CPU_SAMPLE_SECONDS) -> Optional[float]:
    """Measured CPU utilisation over *sample_seconds*, clamped to 0..100."""
    first = cpu_times()
    if first is None:
        return None
    time.sleep(max(0.0, sample_seconds))
    second = cpu_times()
    if second is None:
        return None
    idle_delta = second[0] - first[0]
    total_delta = (second[1] - first[1]) + (second[2] - first[2])
    if total_delta <= 0:
        return None
    usage = 100.0 * (total_delta - idle_delta) / total_delta
    return round(max(0.0, min(100.0, usage)), 1)


def uptime_seconds() -> Optional[float]:
    """Seconds since boot via ``GetTickCount64`` (immune to the 49-day wrap)."""
    if winapi.GetTickCount64 is None:
        return None
    try:
        return round(winapi.GetTickCount64() / 1000.0, 3)
    except Exception:  # pragma: no cover - defensive
        return None


def boot_epoch() -> Optional[float]:
    """Wall-clock epoch second of the last boot."""
    uptime = uptime_seconds()
    if uptime is None:
        return None
    return time.time() - uptime


def disks() -> List[Tuple[str, float, float]]:
    """``(mount, total_gb, free_gb)`` for every fixed drive."""
    rows: List[Tuple[str, float, float]] = []
    if winapi.GetLogicalDrives is None:
        return rows
    mask = winapi.GetLogicalDrives()
    for index in range(26):
        if not (mask >> index) & 1:
            continue
        root = "%s:\\" % chr(ord("A") + index)
        if winapi.GetDriveTypeW is not None and winapi.GetDriveTypeW(root) != winapi.DRIVE_FIXED:
            continue  # removable, network and optical drives are not "the disk"
        free = ctypes.c_ulonglong()
        total = ctypes.c_ulonglong()
        available = ctypes.c_ulonglong()
        if not winapi.GetDiskFreeSpaceExW(
                root, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
            continue
        rows.append((root, common.gb(total.value), common.gb(free.value)))
    return rows


def top_processes(limit: int = 5) -> List[Tuple[str, float]]:
    """``(name, working_set_mb)`` for the *limit* largest processes."""
    rows: List[Tuple[str, float]] = []
    if winapi.CreateToolhelp32Snapshot is None:
        return rows
    snapshot = winapi.CreateToolhelp32Snapshot(winapi.TH32CS_SNAPPROCESS, 0)
    if not _check_handle(snapshot):
        return rows
    try:
        entry = winapi.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(winapi.PROCESSENTRY32W)
        if not winapi.Process32FirstW(snapshot, ctypes.byref(entry)):
            return rows
        while True:
            pid = int(entry.th32ProcessID)
            if pid:
                usage = _working_set_mb(pid)
                if usage is not None:
                    rows.append((entry.szExeFile or "?", usage))
            if not winapi.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        winapi.CloseHandle(snapshot)
    rows.sort(key=lambda item: item[1], reverse=True)
    return rows[:limit]


def _working_set_mb(pid: int) -> Optional[float]:
    """Working set of *pid* in MB, ``None`` when the process cannot be opened.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` is enough on Vista+ and succeeds for
    most other users' processes, so the table is not just this node's children.
    """
    if winapi.OpenProcess is None or winapi.GetProcessMemoryInfo is None:
        return None
    handle = winapi.OpenProcess(
        winapi.PROCESS_QUERY_LIMITED_INFORMATION | winapi.PROCESS_VM_READ, False, pid)
    if not _check_handle(handle):
        handle = winapi.OpenProcess(winapi.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not _check_handle(handle):
        return None
    try:
        counters = winapi.PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(winapi.PROCESS_MEMORY_COUNTERS)
        if not winapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return round(counters.WorkingSetSize / common.MB, 1)
    finally:
        winapi.CloseHandle(handle)


def info_blocks() -> Dict[str, str]:
    """The four ``system_info`` blocks, rendered the same way as every platform."""
    usage = memory()
    cpu = cpu_percent()
    uptime = uptime_seconds()
    return {
        "disks": common.disks_block(disks()),
        "memory": common.memory_block(usage["total_mb"], usage["used_mb"], usage["avail_mb"]),
        "load": common.load_block(cpu, common.cores(), common.iso_local(boot_epoch()), uptime),
        "top_processes": common.processes_block(top_processes()),
    }


def vitals() -> Dict[str, Any]:
    """``system_vitals`` payload: the same shape on every platform."""
    usage = memory()
    cpu = cpu_percent()
    total_mb = usage["total_mb"]
    used_mb = usage["used_mb"]
    # Windows has no load average. Reporting the measured utilisation in all
    # three slots is honest; a fabricated 0.0 used to be read as "idle".
    load = cpu if cpu is not None else 0.0
    disk = common.disk_usage("C:\\") or (0.0, 0.0, 0.0)
    total_gb, used_gb, free_gb = disk
    return {
        "hostname": common.hostname(),
        "os": "Windows",
        "cpu_load": {"1m": float(load), "5m": float(load), "15m": float(load)},
        "ram": {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": usage["avail_mb"],
            "used_pct": common.pct(used_mb, total_mb),
        },
        "disk": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": common.pct(used_gb, total_gb),
        },
    }
