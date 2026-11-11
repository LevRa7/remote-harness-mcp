"""core/platforms/windows - the Windows backend.

Windows is the platform most likely to be *clean*: an image just rolled out by
an IT department, with PowerShell possibly removed by policy, no Git, no Python
on ``PATH`` except the one the node itself uses, and no WSL.  Nothing in this
package needs any of those: the Win32 API through ``ctypes`` is part of the OS.

What this backend owns:

* the command shell (PowerShell 5.1, ``cmd.exe``, or Git Bash when the operator
  asks for it and it is installed);
* telemetry (memory, CPU, disks, processes, uptime) from kernel calls;
* process liveness and tree termination;
* desktop capabilities a Windows node actually has: screenshots and clipboard.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional, Tuple

from core.platforms import common, encoding, shell
from core.platforms.base import PlatformBackend
from core.platforms.windows import clipboard as _clipboard
from core.platforms.windows import process as _process
from core.platforms.windows import screen as _screen
from core.platforms.windows import telemetry as _telemetry
from core.platforms.windows import winapi

#: Hide the console window of every child process: the node runs hidden, and a
#: flashing window per command would be the only thing the user sees.
POPEN_FLAGS = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0x08000000) | getattr(
    __import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0x00000200)


class WindowsBackend(PlatformBackend):
    scenario = "windows"
    label = "Windows"

    def __init__(self) -> None:
        self._shell_cache: Optional[Tuple[str, List[str]]] = None
        self._shell_key: Optional[str] = None
        self._encodings: Optional[List[str]] = None

    # -- command execution -------------------------------------------------

    def shell(self) -> Tuple[str, List[str]]:
        """PowerShell by default, ``cmd.exe`` when it is missing, bash on request.

        ``MESH_SHELL`` selects explicitly (``cmd``, ``git-bash``); the cache is
        keyed on that value so changing it takes effect without a restart.
        """
        override = (os.environ.get("MESH_SHELL") or "").strip().lower()
        if self._shell_cache is None or self._shell_key != override:
            self._shell_cache = shell.resolve(
                is_windows=True,
                which=shutil.which,
                environ=os.environ,
                isfile=os.path.isfile,
                override=override,
                comspec=os.environ.get("COMSPEC"),
                powershell=shell.powershell_argv(shutil.which),
            )
            self._shell_key = override
        return self._shell_cache

    def popen_flags(self) -> int:
        return POPEN_FLAGS

    def child_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        # A Python child writing to a pipe uses the ANSI code page, which is
        # neither UTF-8 nor the OEM page the console shows. Pin it.
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        return env

    def output_encodings(self) -> List[str]:
        """UTF-8 first, then the OEM console page, then ANSI - see the module doc."""
        if self._encodings is None:
            self._encodings = encoding.candidate_encodings(
                is_windows=True, code_pages=code_pages)
        return self._encodings

    # -- telemetry ---------------------------------------------------------

    def uptime_seconds(self) -> Optional[float]:
        return _telemetry.uptime_seconds()

    def vitals(self) -> Dict[str, object]:
        return _telemetry.vitals()

    def info_blocks(self) -> Dict[str, str]:
        return _telemetry.info_blocks()

    def wallpaper(self) -> str:
        """Current wallpaper from the registry (no shell, no WMI)."""
        try:
            import winreg
        except Exception:
            return ""
        path = ""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
                value, _kind = winreg.QueryValueEx(key, "WallPaper")
                path = str(value or "").strip().strip('"')
        except Exception:
            path = ""
        if not path:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers",
                ) as key:
                    history, _kind = winreg.QueryValueEx(key, "BackgroundHistoryPathList")
                    for candidate in reversed(list(history or [])):
                        candidate = str(candidate).strip().strip('"')
                        if candidate and os.path.isfile(candidate):
                            path = candidate
                            break
            except Exception:
                pass
        if not path:
            transcoded = os.path.join(
                os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Themes",
                "TranscodedWallpaper")
            if os.path.isfile(transcoded):
                path = transcoded
        return os.path.expandvars(path) if path else ""

    # -- processes ---------------------------------------------------------

    def pid_alive(self, pid: int, start_time: Optional[int] = None) -> bool:
        return _process.alive(pid, start_time)

    def pid_start_time(self, pid: int) -> Optional[int]:
        return _process.start_time_of(pid)

    def kill_tree(self, pid: int) -> None:
        _process.kill_tree(pid)

    # -- desktop capabilities ---------------------------------------------

    def screenshot_png(self) -> Optional[bytes]:
        return _screen.screenshot_png()

    def clipboard_get(self) -> Optional[str]:
        return _clipboard.get_text()

    def clipboard_set(self, text: str) -> bool:
        return _clipboard.set_text(text)

    def capabilities(self) -> Dict[str, bool]:
        return {
            "shell": bool(self.shell()[1]),
            "powershell": shell.powershell_argv(shutil.which) is not None,
            "git_bash": shell.git_bash(shutil.which, os.path.isfile, os.environ) is not None,
            "vitals": True,
            "info_blocks": True,
            "process_tree": True,
            "screenshot": winapi.IS_WINDOWS and winapi.GetDC is not None,
            "clipboard": winapi.IS_WINDOWS and winapi.OpenClipboard is not None,
        }


def code_pages() -> List[str]:
    """OEM then ANSI code page, as ``cp<n>`` names codecs understand."""
    pages: List[str] = []
    if winapi.kernel32 is None:
        return pages
    for getter in ("GetOEMCP", "GetACP"):
        try:
            code_page = int(getattr(winapi.kernel32, getter)())
        except Exception:
            continue
        if code_page > 0:
            pages.append("cp%d" % code_page)
    return pages


__all__ = ["WindowsBackend", "POPEN_FLAGS", "code_pages"]
