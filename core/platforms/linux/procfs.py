"""core/platforms/linux/procfs.py - host facts read from /proc and /sys.

Every value here comes from a file the kernel maintains, so the node needs no
``df``, ``free``, ``ps``, ``uptime`` or ``top``: those are external programs that
a container may not have, that print localized text, and that cost a process per
call.  Reading ``/proc/meminfo`` is one ``open``.

The root directories are constructor arguments rather than hard-coded paths, for
two reasons:

* a unit test can build a fake ``/proc`` tree from fixtures and assert the parser
  without needing a Linux machine (this is how the Linux backend is tested on
  Windows);
* an Android phone exposes the same files, so :class:`ProcFS` is shared and the
  Android backend only adds ``/sys`` sources on top.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from core.platforms import common

#: Fallback page size when ``sysconf`` is unavailable (4 KiB is the Android and
#: x86 default; a wrong value only shifts the process-memory column).
DEFAULT_PAGE_SIZE = 4096


def page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return DEFAULT_PAGE_SIZE


def clock_ticks() -> int:
    """``USER_HZ`` - the unit of ``utime``/``stime`` in ``/proc/<pid>/stat``."""
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return 100


def parse_meminfo(text: str) -> Dict[str, float]:
    """``/proc/meminfo`` as ``{key: kB}`` (the unit the file always uses)."""
    values: Dict[str, float] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            values[key.strip()] = float(parts[0])
        except ValueError:
            continue
    return values


def parse_cpu_line(line: str) -> Optional[Tuple[float, float]]:
    """One ``cpu`` line as ``(idle_units, total_units)``.

    ``idle`` includes ``iowait``: a disk-bound server is not a busy CPU, and the
    ``top`` everyone compares against counts it as idle too.
    """
    fields = line.split()
    if len(fields) < 5 or fields[0] != "cpu":
        return None
    try:
        numbers = [float(value) for value in fields[1:]]
    except ValueError:
        return None
    user, nice, system, idle = numbers[0], numbers[1], numbers[2], numbers[3]
    iowait = numbers[4] if len(numbers) > 4 else 0.0
    total = sum(numbers)
    return (idle + iowait, total)


def parse_stat(text: str) -> Optional[Dict[str, int]]:
    """``/proc/<pid>/stat`` into the fields this package needs.

    ``comm`` is wrapped in parentheses and may itself contain spaces or
    parentheses (``(sd-pam)``, ``(Web Content)``), so the field list starts after
    the *last* ``)`` - splitting on whitespace anywhere earlier shifts every
    later field by however many words the process name contained.
    """
    try:
        close = text.rindex(")")
        opening = text.index("(")
    except ValueError:
        return None
    if close <= opening:
        return None
    fields = text[close + 1:].split()
    if len(fields) < 22:
        return None

    def value(index: int) -> int:
        """Field *index* of the stat line (1-based, as documented).

        Field 3 (``state``) is ``fields[0]``, so the offset is three.
        """
        try:
            return int(fields[index - 3])
        except (IndexError, ValueError):
            return 0

    return {
        "pid": value(1) or 0,
        "ppid": value(4),
        "utime": value(14),
        "stime": value(15),
        "starttime": value(22),
        "rss_pages": value(24),
    }


class ProcFS(object):
    """A parameterisable view of ``/proc`` (and ``/sys`` when asked)."""

    def __init__(self, root: str = "/proc", sysroot: str = "/sys") -> None:
        self.root = root
        self.sysroot = sysroot

    # -- helpers -----------------------------------------------------------

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def sys_path(self, *parts: str) -> str:
        return os.path.join(self.sysroot, *parts)

    def available(self) -> bool:
        return os.path.isdir(self.root)

    def pids(self) -> List[int]:
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        return sorted(int(name) for name in names if name.isdigit())

    # -- host sections -----------------------------------------------------

    def meminfo(self) -> Dict[str, float]:
        return parse_meminfo(common.read_text(self.path("meminfo"), 65536))

    def cpu_times(self) -> Optional[Tuple[float, float]]:
        for line in common.read_text(self.path("stat"), 262144).splitlines():
            if line.startswith("cpu "):
                return parse_cpu_line(line)
        return None

    def cpu_percent(self, previous: Optional[Tuple[float, float]] = None,
                    sample_seconds: float = 0.0) -> Optional[float]:
        """CPU utilisation between two samples; samples once when needed."""
        first = previous or self.cpu_times()
        if first is None:
            return None
        if sample_seconds > 0:
            import time
            time.sleep(sample_seconds)
        second = self.cpu_times()
        if second is None:
            return None
        idle_delta = second[0] - first[0]
        total_delta = second[1] - first[1]
        if total_delta <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta)), 1)

    def uptime(self) -> Optional[float]:
        text = common.read_text(self.path("uptime"), 128).strip()
        if not text:
            return None
        try:
            return float(text.split()[0])
        except (IndexError, ValueError):
            return None

    def cpu_times_per_core(self) -> List[Tuple[float, float]]:
        """Per-core ``(idle, total)`` rows, for a caller that wants one core."""
        rows: List[Tuple[float, float]] = []
        for line in common.read_text(self.path("stat"), 262144).splitlines():
            if not line.startswith("cpu") or line.startswith("cpu "):
                continue
            name, _, rest = line.partition(" ")
            parsed = parse_cpu_line("cpu " + rest)
            if parsed:
                rows.append(parsed)
        return rows

    # -- processes ---------------------------------------------------------

    def stat_of(self, pid: int) -> Optional[Dict[str, Any]]:
        """Parsed ``/proc/<pid>/stat`` plus the process name in ``name``."""
        text = common.read_text(self.path(str(pid), "stat"), 8192)
        if not text:
            return None
        parsed = parse_stat(text)
        if parsed is None:
            return None
        try:
            close = text.rindex(")")
            parsed["name"] = text[text.index("(") + 1:close]
        except ValueError:
            parsed["name"] = ""
        return parsed

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        info = self.stat_of(pid)
        if info is None:
            return False
        state = common.read_text(self.path(str(pid), "stat"), 8192)
        # A zombie still has a /proc entry but is not a running process.
        try:
            fields = state[state.rindex(")") + 2:].split()
            if fields and fields[0] == "Z":
                return False
        except (ValueError, IndexError):
            pass
        if start_time is not None and info["starttime"] and int(info["starttime"]) != int(start_time):
            return False
        return True

    def pid_start_time(self, pid: int) -> Optional[int]:
        info = self.stat_of(pid)
        return int(info["starttime"]) if info else None

    def parent_map(self) -> Dict[int, List[int]]:
        mapping: Dict[int, List[int]] = {}
        for pid in self.pids():
            info = self.stat_of(pid)
            if not info:
                continue
            mapping.setdefault(int(info["ppid"]), []).append(pid)
        return mapping

    def descendants(self, pid: int, mapping: Optional[Dict[int, List[int]]] = None) -> List[int]:
        tree = mapping if mapping is not None else self.parent_map()
        ordered: List[int] = []
        pending = list(tree.get(int(pid), ()))
        seen = {int(pid)}
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            ordered.append(current)
            pending.extend(tree.get(current, ()))
        ordered.reverse()
        return ordered

    def rss_mb(self, pid: int) -> Optional[float]:
        info = self.stat_of(pid)
        if not info:
            return None
        return round(info["rss_pages"] * page_size() / common.MB, 1)

    def top_processes(self, limit: int = 5) -> List[Tuple[str, float]]:
        rows: List[Tuple[str, float]] = []
        for pid in self.pids():
            info = self.stat_of(pid)
            if not info:
                continue
            memory = info["rss_pages"] * page_size() / common.MB
            rows.append((info.get("name") or "?", round(memory, 1)))
        rows.sort(key=lambda item: item[1], reverse=True)
        return rows[:limit]

    def kill_tree(self, pid: int) -> None:
        """SIGKILL the whole subtree, children first. Never raises."""
        import signal

        for child in self.descendants(pid):
            try:
                os.kill(child, signal.SIGKILL)
            except OSError:
                continue
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            return


__all__ = [
    "DEFAULT_PAGE_SIZE", "ProcFS", "clock_ticks", "page_size", "parse_cpu_line",
    "parse_meminfo", "parse_stat",
]
