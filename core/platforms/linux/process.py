"""core/platforms/linux/process.py - liveness and tree killing through /proc.

No ``ps`` and no ``killall``: ``/proc/<pid>/stat`` says whether a pid exists and
when it started, and ``os.kill`` with the signal number is a syscall.  The
PID-reuse guard matters for job bookkeeping: a job that outlived its process
would otherwise be reported as running forever because an unrelated process got
the same pid.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from core.platforms.linux.procfs import ProcFS

#: Shared view; ``ProcFS`` is stateless apart from the two root paths.
_PROC = ProcFS()


def alive(pid: int, start_time: Optional[int] = None) -> bool:
    return _PROC.pid_alive(pid, start_time)


def start_time_of(pid: int) -> Optional[int]:
    return _PROC.pid_start_time(pid)


def parent_map() -> Dict[int, List[int]]:
    return _PROC.parent_map()


def descendants(pid: int, mapping: Optional[Dict[int, List[int]]] = None) -> List[int]:
    return _PROC.descendants(pid, mapping)


def kill_tree(pid: int) -> None:
    _PROC.kill_tree(pid)


__all__ = ["alive", "descendants", "kill_tree", "parent_map", "start_time_of"]
