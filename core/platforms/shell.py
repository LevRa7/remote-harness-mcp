"""core/platforms/shell.py - which shell runs a command, as a pure decision.

This used to live inside ``core/mcp_tools.py`` mixed with the cache and the
environment reads.  Keeping the decision pure means it can be tested for every
combination of platform, override and installed shell without starting a
process or faking ``os.name``.

The rule itself is a policy, not an implementation detail:

* **POSIX** runs ``bash`` when it exists, ``/bin/sh`` otherwise.  A server has no
  reason to be picky: both understand what a model writes.
* **Windows never defaults to bash.**  A stock box has no bash; the ``bash`` on
  ``PATH`` is usually the WSL launcher, which runs the command in a different
  filesystem and process namespace and fails in ways that look like a broken
  node.  PowerShell ships with the OS, so it is the default, and ``cmd.exe`` is
  the fallback for a box where PowerShell was removed or blocked by policy.
* ``MESH_SHELL`` lets an operator override all of that, and ``git-bash`` is
  honoured only when Git for Windows is actually installed.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

#: "The caller did not resolve this yet", as opposed to "the host has none".
_UNSET = object()

#: Shell names this module can return.
SHELL_POWERSHELL = "powershell"
SHELL_CMD = "cmd"
SHELL_GIT_BASH = "git-bash"
SHELL_BASH = "bash"
SHELL_SH = "sh"


def powershell_argv(which: Callable[[str], Optional[str]]) -> Optional[List[str]]:
    """argv prefix of PowerShell, or ``None`` when it is not installed.

    ``pwsh`` (PowerShell 7) wins over ``powershell`` (5.1) when both exist; the
    flags make a one-shot command behave like a script call - no profile, no
    banner, no interactive prompt and no execution-policy surprise.
    """
    found = which("pwsh") or which("powershell")
    if not found:
        return None
    return [
        found,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
    ]


def cmd_argv(comspec: Optional[str]) -> List[str]:
    """argv prefix of cmd.exe (present on every Windows).

    ``/d`` skips AutoRun, ``/s`` keeps the quoting of the whole command intact
    and ``/c`` runs and exits.
    """
    return [comspec or "cmd.exe", "/d", "/s", "/c"]


def git_bash(which: Callable[[str], Optional[str]],
             isfile: Callable[[str], bool],
             environ: dict) -> Optional[str]:
    """Git for Windows' ``bash.exe``, never ``System32\\bash.exe`` (the WSL stub)."""
    import os

    roots = [environ.get(key) for key in
             ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA")]
    candidates: List[str] = []
    for root in filter(None, roots):
        candidates.append(os.path.join(root, "Git", "bin", "bash.exe"))
        candidates.append(os.path.join(root, "Programs", "Git", "bin", "bash.exe"))
    found = which("git")
    if found:
        # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(found)), "bin", "bash.exe"))
    for path in candidates:
        try:
            if isfile(path):
                return path
        except Exception:
            continue
    return None


def resolve(*, is_windows: bool, which: Callable[[str], Optional[str]],
            environ: dict, isfile: Callable[[str], bool],
            override: str = "", comspec: Optional[str] = None,
            powershell: Any = _UNSET,
            bash_path: Any = _UNSET) -> Tuple[str, List[str]]:
    """The ``(name, argv prefix)`` pair for this host.

    ``powershell`` and ``bash_path`` are injectable so the caller can supply
    already-resolved values, and the default is a sentinel rather than ``None``:
    passing ``None`` means "this host has no PowerShell / no Git Bash", which is a
    different fact from "the caller did not look" - conflating the two is how a
    Windows box with PowerShell removed silently went back to spawning PowerShell.
    """
    override = (override or "").strip().lower()
    if not is_windows:
        bash = which("bash")
        if bash:
            return (SHELL_BASH, [bash, "-c"])
        return (SHELL_SH, ["/bin/sh", "-c"])

    if override in ("cmd", "cmd.exe"):
        return (SHELL_CMD, cmd_argv(comspec))
    if override in ("git-bash", "gitbash", "bash"):
        if bash_path is not _UNSET:
            found = bash_path
        else:
            found = git_bash(which, isfile, environ)
        if found:
            return (SHELL_GIT_BASH, [found, "-c"])
        # Asked for a bash that is not installed: fall back to the default
        # rather than failing every command.
        ps = powershell if powershell is not _UNSET else powershell_argv(which)
        return (SHELL_POWERSHELL, ps) if ps else (SHELL_CMD, cmd_argv(comspec))

    ps = powershell if powershell is not _UNSET else powershell_argv(which)
    if ps:
        return (SHELL_POWERSHELL, ps)
    return (SHELL_CMD, cmd_argv(comspec))


__all__ = [
    "SHELL_BASH",
    "SHELL_CMD",
    "SHELL_GIT_BASH",
    "SHELL_POWERSHELL",
    "SHELL_SH",
    "cmd_argv",
    "git_bash",
    "powershell_argv",
    "resolve",
]
