"""core/platforms/windows/process.py - process liveness and tree killing via Win32.

``tasklist`` and ``taskkill`` would be simpler, but they are external programs
whose output format and availability differ between Windows SKUs (``taskkill``
is missing from some minimal images, and its exit codes were localized text
until recently).  A node that cannot tell whether its own child is alive is
broken, so this uses the same kernel calls the old POSIX code uses ``/proc``
for:

* liveness - ``OpenProcess`` + ``GetExitCodeProcess`` (``STILL_ACTIVE``);
* start marker - ``GetProcessTimes`` creation time, the PID-reuse guard that
  ``/proc/<pid>/stat`` field 22 provides on Linux;
* tree - ``Toolhelp32`` parent ids, terminated children-first.
"""

from __future__ import annotations

import ctypes
from typing import Dict, List, Optional

from core.platforms.windows import winapi


def _open(pid: int, access: int):
    if winapi.OpenProcess is None:
        return None
    try:
        handle = winapi.OpenProcess(access, False, int(pid))
    except Exception:  # pragma: no cover - defensive
        return None
    if not handle or handle == winapi.INVALID_HANDLE_VALUE:
        return None
    return handle


def alive(pid: int, start_time: Optional[int] = None) -> bool:
    """True when *pid* is running (and, when given, still the same process)."""
    if not pid:
        return False
    handle = _open(pid, winapi.PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return False
    try:
        code = ctypes.c_ulong(0)
        if not winapi.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        if int(code.value) != winapi.STILL_ACTIVE:
            return False
    finally:
        winapi.CloseHandle(handle)
    if start_time is not None:
        current = start_time_of(pid)
        if current is not None and int(current) != int(start_time):
            return False  # the pid was reused by a different process
    return True


def start_time_of(pid: int) -> Optional[int]:
    """Creation time in 100 ns units, used as an opaque PID-reuse guard."""
    handle = _open(pid, winapi.PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return None
    try:
        created, exited, kernel, user = (winapi.FILETIME(), winapi.FILETIME(),
                                         winapi.FILETIME(), winapi.FILETIME())
        if not winapi.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return int(created.value())
    finally:
        winapi.CloseHandle(handle)


def parent_map() -> Dict[int, List[int]]:
    """``pid -> [child pids]`` for every process this account can enumerate."""
    mapping: Dict[int, List[int]] = {}
    if winapi.CreateToolhelp32Snapshot is None:
        return mapping
    snapshot = winapi.CreateToolhelp32Snapshot(winapi.TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == winapi.INVALID_HANDLE_VALUE:
        return mapping
    try:
        entry = winapi.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(winapi.PROCESSENTRY32W)
        if not winapi.Process32FirstW(snapshot, ctypes.byref(entry)):
            return mapping
        while True:
            child = int(entry.th32ProcessID)
            parent = int(entry.th32ParentProcessID)
            if child and child != parent:
                mapping.setdefault(parent, []).append(child)
            if not winapi.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        winapi.CloseHandle(snapshot)
    return mapping


def descendants(pid: int, mapping: Optional[Dict[int, List[int]]] = None) -> List[int]:
    """Every descendant of *pid*, deepest first (so a parent cannot respawn)."""
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


def kill(pid: int, exit_code: int = 1) -> bool:
    """``TerminateProcess`` on one pid. False when it could not be opened.

    ``PROCESS_TERMINATE`` is the right to end the process and is *not* implied by
    the query rights - opening with only ``PROCESS_QUERY_LIMITED_INFORMATION``
    succeeds and then ``TerminateProcess`` fails with "access denied", which
    looks exactly like a process that refused to die.
    """
    handle = _open(pid, winapi.PROCESS_TERMINATE | winapi.PROCESS_QUERY_LIMITED_INFORMATION
                   | winapi.PROCESS_VM_READ)
    if handle is None:
        handle = _open(pid, winapi.PROCESS_TERMINATE | winapi.PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return False
    try:
        return bool(winapi.TerminateProcess(handle, int(exit_code)))
    finally:
        winapi.CloseHandle(handle)


def kill_tree(pid: int) -> None:
    """Terminate *pid* and every descendant, children first, never raising.

    The parent is killed last: killing it first would orphan the children into
    ``svchost`` and they would survive the job.
    """
    tree = parent_map()
    for child in descendants(pid, tree):
        kill(child)
    kill(pid)


__all__ = ["alive", "descendants", "kill", "kill_tree", "parent_map", "start_time_of"]
