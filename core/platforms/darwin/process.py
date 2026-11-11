"""core/platforms/darwin/process.py - process liveness and trees through libproc.

macOS has no ``/proc``; ``libproc`` is the system API behind ``ps``, ``lsof`` and
Activity Monitor, and it is what this module uses:

* ``proc_listpids(PROC_ALL_PIDS)`` - every pid;
* ``proc_pidinfo(pid, PROC_PIDTBSDINFO)`` - parent, uid, start time, so a job's
  pid can be checked and a whole tree walked;
* liveness is "``proc_pidinfo`` still answers", plus the start-time guard against
  PID reuse (a recycled pid is a different process).

Nothing here shells out to ``ps``/``kill``: those are external programs with
localized output, and the node must not depend on them.
"""

from __future__ import annotations

import ctypes
import os
import signal
from typing import Dict, List, Optional

#: ``proc_listpids`` flavor: every pid on the system.
PROC_ALL_PIDS = 1
#: ``proc_pidinfo`` flavor: ``struct proc_bsdinfo``.
PROC_PIDTBSDINFO = 3


class ProcBsdInfo(ctypes.Structure):
    """``struct proc_bsdinfo`` from ``libproc.h`` (the fields this module reads)."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


_LIB = None
_LOADED = False


def load():
    """``libproc.dylib``, or ``None`` off macOS."""
    global _LIB, _LOADED
    if not _LOADED:
        _LOADED = True
        try:
            _LIB = ctypes.CDLL("/usr/lib/libproc.dylib")
        except OSError:
            _LIB = None
    return _LIB


def _prototype(lib) -> None:
    lib.proc_listpids.restype = ctypes.c_int
    lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                 ctypes.c_void_p, ctypes.c_int]


def info_for(pid: int) -> Optional[ProcBsdInfo]:
    """``proc_bsdinfo`` for *pid*, or ``None`` when it is gone or unreadable."""
    lib = load()
    if lib is None:
        return None
    try:
        _prototype(lib)
        info = ProcBsdInfo()
        size = ctypes.sizeof(ProcBsdInfo)
        written = lib.proc_pidinfo(int(pid), PROC_PIDTBSDINFO, 0,
                                   ctypes.byref(info), size)
        if written <= 0:
            return None
        return info
    except Exception:
        return None


def alive(pid: int, start_time: Optional[int] = None) -> bool:
    info = info_for(pid)
    if info is None:
        return False
    if start_time is not None:
        try:
            if int(info.pbi_start_tvsec) != int(start_time):
                return False
        except (TypeError, ValueError):
            pass
    return True


def start_time_of(pid: int) -> Optional[int]:
    info = info_for(pid)
    return int(info.pbi_start_tvsec) if info is not None else None


def pids() -> List[int]:
    lib = load()
    if lib is None:
        return []
    try:
        _prototype(lib)
        needed = lib.proc_listpids(PROC_ALL_PIDS, 0, None, 0)
        if needed <= 0:
            return []
        buffer = (ctypes.c_int * (needed // ctypes.sizeof(ctypes.c_int)))()
        written = lib.proc_listpids(PROC_ALL_PIDS, 0, ctypes.byref(buffer), needed)
        if written <= 0:
            return []
        count = written // ctypes.sizeof(ctypes.c_int)
        return [int(buffer[index]) for index in range(count) if int(buffer[index]) > 0]
    except Exception:
        return []


def parent_map() -> Dict[int, List[int]]:
    mapping: Dict[int, List[int]] = {}
    for pid in pids():
        info = info_for(pid)
        if info is None:
            continue
        mapping.setdefault(int(info.pbi_ppid), []).append(pid)
    return mapping


def descendants(pid: int, mapping: Optional[Dict[int, List[int]]] = None) -> List[int]:
    tree = mapping if mapping is not None else parent_map()
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


def kill_tree(pid: int) -> None:
    """SIGKILL the subtree, children first. Never raises."""
    for child in descendants(pid):
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:
            continue
    try:
        os.kill(int(pid), signal.SIGKILL)
    except OSError:
        return


__all__ = [
    "PROC_ALL_PIDS", "PROC_PIDTBSDINFO", "ProcBsdInfo", "alive", "descendants",
    "info_for", "kill_tree", "load", "parent_map", "pids", "start_time_of",
]
