"""core/platforms/linux/telemetry.py - host metrics from /proc, no external tools.

``system_info`` used to shell out to ``df -h``, ``free -h``, ``uptime`` and
``ps``; a container without coreutils, a phone, and a server with a localized
``LC_MESSAGES`` all returned different (or no) text.  The kernel files answer the
same questions in microseconds and in a format this module controls.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from core.platforms import common
from core.platforms.linux.procfs import ProcFS, page_size


def root_filesystem() -> str:
    """The mount worth reporting: the node's own filesystem when it is mounted.

    On a server that is ``/``; on a container where ``/`` is the host's overlay
    but ``$HOME`` is a real volume, ``$HOME`` is the useful number. Reporting
    both would double the table without adding a decision.
    """
    home = os.path.expanduser("~")
    try:
        if home and os.path.isdir(home) and os.stat("/").st_dev != os.stat(home).st_dev:
            return home
    except OSError:
        pass
    return "/"


def disks(limit: int = 4) -> List[Tuple[str, float, float]]:
    """``(mount, total_gb, free_gb)`` for the root filesystem and real mounts.

    ``/proc/mounts`` is read directly instead of calling ``df``; pseudo
    filesystems (proc, sysfs, tmpfs, overlay of a container) are skipped because
    their "size" is a kernel detail, not storage the user can free.
    """
    rows: List[Tuple[str, float, float]] = []
    seen = set()
    primary = root_filesystem()
    candidates = [primary, "/"]
    for mount in _real_mounts():
        candidates.append(mount)
    for mount in candidates:
        if mount in seen:
            continue
        seen.add(mount)
        usage = common.disk_usage(mount)
        if usage is None or usage[0] <= 0:
            continue
        rows.append((mount, usage[0], usage[2]))
        if len(rows) >= limit:
            break
    return rows


#: Filesystem types whose "size" is not storage: kernel interfaces and RAM disks.
_SKIP_FSTYPES = frozenset((
    "proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "pstore", "bpf",
    "tracefs", "debugfs", "securityfs", "configfs", "fusectl", "mqueue",
    "hugetlbfs", "autofs", "binfmt_misc", "rpc_pipefs", "nsfs", "squashfs",
    "overlay", "tmpfs", "ramfs", "efivarfs", "selinuxfs",
))


def _real_mounts() -> List[str]:
    """Mount points from ``/proc/mounts`` that represent real storage."""
    mounts: List[str] = []
    for line in common.read_text("/proc/mounts", 262144).splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount, fstype = parts[1], parts[2]
        if fstype in _SKIP_FSTYPES:
            continue
        if mount.startswith(("/snap/", "/var/lib/docker/", "/run/")):
            continue
        if mount not in mounts:
            mounts.append(mount)
    return mounts


def memory() -> Dict[str, float]:
    """RAM in MB, with the ``used`` definition every ``free -h`` user expects."""
    info = ProcFS().meminfo()
    total_kb = info.get("MemTotal", 0.0)
    available_kb = info.get("MemAvailable")
    if available_kb is None:
        # Kernels before 3.14 have no MemAvailable: approximate it the way
        # "free" does, from free + buffers + reclaimable cache.
        available_kb = (info.get("MemFree", 0.0) + info.get("Buffers", 0.0)
                        + info.get("Cached", 0.0) + info.get("SReclaimable", 0.0))
    total_mb = round(total_kb / 1024.0, 1)
    available_mb = round(available_kb / 1024.0, 1)
    cached_mb = round((info.get("Cached", 0.0) + info.get("SReclaimable", 0.0)) / 1024.0, 1)
    return {
        "total_mb": total_mb,
        "avail_mb": available_mb,
        "used_mb": round(max(0.0, total_mb - available_mb), 1),
        "cached_mb": cached_mb,
    }


def info_blocks() -> Dict[str, str]:
    """The four ``system_info`` blocks, rendered like every other platform."""
    usage = memory()
    return {
        "disks": common.disks_block(disks()),
        "memory": common.memory_block(
            usage["total_mb"], usage["used_mb"], usage["avail_mb"], usage["cached_mb"]),
        "load": common.load_block(
            None, common.cores(), common.iso_local(boot_epoch()),
            ProcFS().uptime(), common.load_average()),
        "top_processes": common.processes_block(ProcFS().top_processes()),
    }


def boot_epoch() -> Optional[float]:
    """Wall-clock epoch second of the last boot."""
    uptime = ProcFS().uptime()
    if uptime is None:
        return None
    import time
    return time.time() - uptime


def vitals() -> Dict[str, Any]:
    """``system_vitals`` payload; the shape is identical on every platform."""
    usage = memory()
    load = common.load_average() or (0.0, 0.0, 0.0)
    disk = common.disk_usage(root_filesystem()) or (0.0, 0.0, 0.0)
    total_gb, used_gb, free_gb = disk
    return {
        "hostname": common.hostname(),
        "os": "Linux",
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


__all__ = ["boot_epoch", "disks", "info_blocks", "memory", "root_filesystem", "vitals"]
