"""core/platforms/darwin/telemetry.py - macOS metrics without external programs.

The Linux backend reads ``/proc``; macOS has none of it. Everything here comes
from the two interfaces the kernel actually exposes to a normal process:

* ``sysctlbyname`` - scalars such as ``hw.memsize``, ``hw.pagesize`` and the
  ``vm.page_*_count`` counters (the same numbers Activity Monitor shows);
* ``libproc`` - the process table, through :mod:`core.platforms.darwin.process`.

No ``ps``/``vm_stat``/``df`` child is spawned: their output is localized and
their columns shift between releases, and parsing them was exactly the
dependency this package exists to remove. ``sysctl`` itself is a syscall
wrapper - calling the syscall directly is the same data without the program.

Every function degrades to "unknown" (0 / empty) on a non-macOS host instead of
raising, because the suite exercises these helpers on Windows and Linux too.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from core.platforms import common
from core.platforms.darwin import process as _process


def _libc():
    """``libSystem`` on macOS, ``None`` elsewhere (sysctlbyname lives there)."""
    if sys.platform != "darwin":
        return None
    try:
        return ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None


def sysctl(name: str, size: int = 8) -> Optional[bytes]:
    """Raw bytes of a sysctl value, or ``None`` when the name is unavailable.

    The caller says how big the value is (a ``uint64`` counter, a ``uint32`` page
    count, a ``struct timeval``): ``sysctlbyname`` reports the real length, and a
    mismatch is treated as "unknown" rather than as a truncated number.
    """
    libc = _libc()
    if libc is None:
        return None
    try:
        buffer = ctypes.create_string_buffer(max(1, int(size)))
        length = ctypes.c_size_t(int(size))
        result = libc.sysctlbyname(
            str(name).encode("ascii"), buffer, ctypes.byref(length), None, 0)
        if result != 0 or length.value <= 0:
            return None
        return bytes(buffer.raw[:length.value])
    except Exception:
        return None


def sysctl_int(name: str, size: int = 8) -> Optional[int]:
    """A sysctl value read as an unsigned little-endian integer."""
    raw = sysctl(name, size)
    if not raw:
        return None
    try:
        if size == 4:
            return int(struct.unpack_from("<I", raw, 0)[0])
        return int(struct.unpack_from("<Q", raw, 0)[0])
    except struct.error:
        return None


def page_size() -> int:
    """The VM page size (4 KiB on Intel, 16 KiB on Apple silicon)."""
    return sysctl_int("hw.pagesize", 4) or 4096


def total_bytes() -> int:
    """Installed RAM in bytes, or 0 when the host does not answer."""
    return sysctl_int("hw.memsize", 8) or 0


def boot_epoch() -> Optional[float]:
    """Wall-clock epoch second of the last boot (``kern.boottime``).

    ``struct timeval`` starts with ``tv_sec`` (an 8-byte long on every supported
    macOS), so the first eight bytes are read directly instead of relying on
    ctypes struct padding.
    """
    raw = sysctl("kern.boottime", 16)
    if not raw or len(raw) < 8:
        return None
    try:
        seconds = struct.unpack_from("<q", raw, 0)[0]
    except struct.error:
        return None
    return float(seconds) if seconds > 0 else None


def uptime_seconds() -> Optional[float]:
    booted = boot_epoch()
    if booted is None:
        return None
    return max(0.0, time.time() - booted)


def memory() -> Dict[str, float]:
    """RAM in MB, with the ``free -h`` definition every user expects.

    Available is ``free + speculative + inactive`` pages: inactive pages are
    reclaimable file cache, which is why ``used`` is below "total - free".
    """
    total = total_bytes()
    page = page_size()
    free_pages = sysctl_int("vm.page_free_count", 4) or 0
    speculative = sysctl_int("vm.page_speculative_count", 4) or 0
    inactive = sysctl_int("vm.page_inactive_count", 4) or 0
    total_mb = round(total / common.MB, 1) if total else 0.0
    available_mb = round((free_pages + speculative + inactive) * page / common.MB, 1)
    if total_mb and available_mb > total_mb:
        available_mb = total_mb
    return {
        "total_mb": total_mb,
        "avail_mb": available_mb,
        "used_mb": round(max(0.0, total_mb - available_mb), 1),
        "cached_mb": round(inactive * page / common.MB, 1),
    }


def disks(limit: int = 4) -> List[Tuple[str, float, float]]:
    """``(mount, total_gb, free_gb)`` for the node's volume.

    macOS mounts every partition under ``/Volumes``; the root volume and the home
    directory are the two numbers a user acts on, and ``shutil.disk_usage`` asks
    ``statfs`` without spawning ``df``.
    """
    rows: List[Tuple[str, float, float]] = []
    seen = set()
    for mount in ("/", os.path.expanduser("~")):
        if not mount or mount in seen:
            continue
        seen.add(mount)
        usage = common.disk_usage(mount)
        if usage is None or usage[0] <= 0:
            continue
        rows.append((mount, usage[0], usage[2]))
        if len(rows) >= limit:
            break
    return rows


def top_processes(limit: int = 5) -> List[Tuple[str, float]]:
    """``(command, resident_mb)`` for the heaviest processes, via libproc."""
    rows: List[Tuple[str, float]] = []
    for pid in _process.pids():
        resident = _process.resident_bytes(pid)
        if resident is None or resident <= 0:
            continue
        name = _process.name_of(pid) or str(pid)
        rows.append((name, round(resident / common.MB, 1)))
    rows.sort(key=lambda row: row[1], reverse=True)
    return rows[:max(1, limit)]


def info_blocks() -> Dict[str, str]:
    """The four ``system_info`` blocks, rendered like every other platform."""
    usage = memory()
    return {
        "disks": common.disks_block(disks()),
        "memory": common.memory_block(
            usage["total_mb"], usage["used_mb"], usage["avail_mb"], usage["cached_mb"]),
        "load": common.load_block(
            None, common.cores(), common.iso_local(boot_epoch()),
            uptime_seconds(), common.load_average()),
        "top_processes": common.processes_block(top_processes()),
    }


def vitals() -> Dict[str, Any]:
    """``system_vitals`` payload; the shape is identical on every platform."""
    usage = memory()
    load = common.load_average() or (0.0, 0.0, 0.0)
    total_gb, used_gb, free_gb = common.disk_usage("/") or (0.0, 0.0, 0.0)
    return {
        "hostname": common.hostname(),
        "os": "macOS",
        "cpu_load": {"1m": float(load[0]), "5m": float(load[1]), "15m": float(load[2])},
        "ram": {
            "total_mb": usage["total_mb"],
            "used_mb": usage["used_mb"],
            "free_mb": usage["avail_mb"],
            "used_pct": common.pct(usage["used_mb"], usage["total_mb"]),
        },
        "disk": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": common.pct(used_gb, total_gb),
        },
    }


__all__ = [
    "boot_epoch", "disks", "info_blocks", "memory", "page_size", "sysctl",
    "sysctl_int", "top_processes", "total_bytes", "uptime_seconds", "vitals",
]
