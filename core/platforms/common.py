"""core/platforms/common.py - helper code that is genuinely portable.

Everything here is standard library and OS-neutral: text blocks are rendered the
same way on every platform so a model that learned the shape of ``system_info``
on a Linux server reads the Windows or Android answer without relearning it.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.platforms.encoding import decode

#: One decimal is enough for a human-readable gigabyte value.
GB = 1024.0 ** 3
MB = 1024.0 ** 2


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def read_text(path: str, limit: int = 1_048_576) -> str:
    """Best-effort read of a small procfs/sysfs file. Empty string on failure."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except Exception:
        return ""


def read_int(path: str) -> Optional[int]:
    """First integer in a procfs/sysfs file, or ``None``."""
    text = read_text(path, 128).strip()
    if not text:
        return None
    try:
        return int(text.split()[0])
    except (IndexError, TypeError, ValueError):
        return None


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN
        return default
    return result


def pct(part: Any, total: Any) -> float:
    """Percentage of *total*, clamped to 0..100, 0.0 when total is unknown."""
    try:
        part_f = float(part)
        total_f = float(total)
    except (TypeError, ValueError):
        return 0.0
    if total_f <= 0:
        return 0.0
    return round(max(0.0, min(100.0, part_f / total_f * 100.0)), 1)


def gb(value_bytes: Any) -> float:
    """Bytes to gigabytes, one decimal (0.0 when the value is not a number)."""
    return round(to_float(value_bytes) / GB, 1)


def mb(value_bytes: Any) -> float:
    return round(to_float(value_bytes) / MB, 1)


def human_gb(gigabytes: float) -> str:
    """``15.4G`` - short enough for a table, precise enough to act on."""
    return "%.1fG" % to_float(gigabytes)


def disk_usage(path: str) -> Optional[Tuple[float, float, float]]:
    """``(total_gb, used_gb, free_gb)`` for the filesystem holding *path*."""
    try:
        usage = shutil.disk_usage(path)
    except Exception:
        return None
    return (gb(usage.total), gb(usage.used), gb(usage.free))


def run_argv(argv: Sequence[str], timeout: float = 10.0,
             cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
             flags: int = 0,
             encodings: Optional[Sequence[str]] = None) -> Tuple[str, str, int, float]:
    """Run an argv list and return ``(stdout, stderr, code, duration)``.

    Used only for the rare child a platform genuinely needs (a shell command the
    model asked for, ``screencap`` on Android); telemetry never goes through
    here, because spawning a program to read a counter is exactly the dependency
    this package exists to remove.
    """
    started = time.time()
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=timeout,
            cwd=cwd or None,
            env=env,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        return "", str(exc), 127, round(time.time() - started, 3)
    except subprocess.TimeoutExpired:
        return "", "timed out after %.0fs" % timeout, 124, round(time.time() - started, 3)
    except Exception as exc:  # pragma: no cover - defensive
        return "", str(exc), 1, round(time.time() - started, 3)
    candidates = list(encodings or ["utf-8"])
    return (
        decode(completed.stdout, candidates),
        decode(completed.stderr, candidates),
        int(completed.returncode),
        round(time.time() - started, 3),
    )


# ---------------------------------------------------------------------------
# Text blocks for system_info
#
# The keys are the same on every platform (disks, memory, load, top_processes)
# and the columns are the same; only the source of the numbers differs. That is
# deliberate: a model that learned to read the Linux block reads the Android and
# Windows ones at no extra cost, and a test can assert the shape on any host.
# ---------------------------------------------------------------------------

def memory_block(total_mb: Any, used_mb: Any, free_mb: Any,
                 cached_mb: Any = None) -> str:
    """A ``free -h`` shaped table; *cached_mb* is appended when the OS knows it."""
    columns = ["total", "used", "free"]
    values = [to_float(total_mb) / 1024.0, to_float(used_mb) / 1024.0, to_float(free_mb) / 1024.0]
    if cached_mb is not None:
        columns.append("cache")
        values.append(to_float(cached_mb) / 1024.0)
    header = "%13s" % "" + "".join("%11s" % name for name in columns)
    row = "Mem:" + "".join("%11s" % human_gb(value) for value in values)
    return header + "\n" + row


def disks_block(rows: Iterable[Tuple[str, float, float]]) -> str:
    """``(mount, total_gb, free_gb)`` rows as one aligned table."""
    lines = ["%-20s%10s%10s%10s%6s" % ("Mount", "Total", "Used", "Free", "Use%")]
    for mount, total, free in rows:
        total_f = to_float(total)
        free_f = to_float(free)
        used_f = max(0.0, total_f - free_f)
        lines.append("%-20s%9.1fG%9.1fG%9.1fG%5.0f%%"
                     % (str(mount)[:20], total_f, used_f, free_f, pct(used_f, total_f)))
    return "\n".join(lines)


def processes_block(rows: Iterable[Tuple[str, float]]) -> str:
    """``(command, memory_mb)`` rows as one aligned table."""
    lines = ["%-26s%10s" % ("COMMAND", "MEM_MB")]
    for name, memory in rows:
        lines.append("%-26s%10.1f" % (str(name)[:26], to_float(memory)))
    return "\n".join(lines)


def load_block(cpu_pct: Optional[float], cores: Optional[int],
               booted: Optional[str], uptime_seconds: Optional[float],
               load_average: Optional[Sequence[float]] = None) -> str:
    """``CPU ... | load ... | cores ... | booted ... | up ...`` on one line."""
    parts: List[str] = []
    if cpu_pct is not None:
        parts.append("CPU %.0f%%" % max(0.0, min(100.0, to_float(cpu_pct))))
    if load_average:
        parts.append("load " + " ".join("%.2f" % to_float(value) for value in load_average))
    if cores:
        parts.append("cores %d" % int(cores))
    if booted:
        parts.append("booted %s" % booted)
    if uptime_seconds is not None:
        parts.append("up %.1f h" % (to_float(uptime_seconds) / 3600.0))
    return " | ".join(parts) or "unavailable"


def iso_local(timestamp: Optional[float]) -> str:
    """Epoch seconds as a local ``YYYY-MM-DD HH:MM`` string."""
    if timestamp is None:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(to_float(timestamp)))
    except Exception:
        return ""


def cores() -> Optional[int]:
    """Logical CPU count, or ``None`` when the OS does not say."""
    try:
        return os.cpu_count() or None
    except Exception:
        return None


def load_average() -> Optional[Tuple[float, float, float]]:
    """1/5/15-minute load average where the OS has one (Windows does not)."""
    try:
        return tuple(round(value, 2) for value in os.getloadavg())  # type: ignore[return-value]
    except Exception:
        return None


__all__ = [
    "GB", "MB", "cores", "disk_usage", "disks_block", "gb", "hostname",
    "human_gb", "iso_local", "load_average", "load_block", "mb", "memory_block",
    "pct", "processes_block", "read_int", "read_text", "run_argv", "to_float",
]
