#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rhm`` - the node's console menu and one-shot settings commands.

Shows the node status and the switches, and switches the live access mode with
Tab / Left / Right:

    read_only   observation only
    full        every call runs without a node-side prompt
    full        no prompts (the operator's explicit choice)

It also sets the agent's working directory, starts the isolated container and
holds the remote-control lock (``r``).  Everything is local: no network, no
token is printed except the link key that is already in the MCP URL.

The same switches without a terminal, for scripts and for restoring the lock::

    rhm status
    rhm mode read_only|full
    rhm cd /path/to/project
    rhm isolation on|off
    rhm remote-control on|off
    rhm pause | rhm resume
    rhm goal "text" | rhm goal | rhm goal done "note" | rhm goal clear

Every path (this menu, those commands, the MCP tool and the cabinet) calls
:func:`core.settings.apply`, so they cannot disagree.

Run: ``python -m core.menu`` (the installer/npm CLI exposes ``rhm``).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from core import control, domain, goal, paths, sandbox, schedule as schedule_module
from core import settings as node_settings, usage

_NOTICE = ""


# ---------------------------------------------------------------------------
# Config, URL and log
# ---------------------------------------------------------------------------

_ANSI = os.name != "nt"

#: Last painted frame. A one-second status refresh must not reprint an identical
#: screen: on a console where clearing silently fails that looked like an
#: infinite scroll of menus.
_LAST_FRAME: Optional[str] = None
#: None = not probed yet, True = Win32 console painting works, False = use ANSI.
_WIN_CONSOLE: Optional[bool] = None

try:
    import ctypes
    from ctypes import wintypes

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _SMALL_RECT(ctypes.Structure):
        _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                    ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]

    class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [("dwSize", _COORD), ("dwCursorPosition", _COORD),
                    ("wAttributes", ctypes.c_ushort), ("srWindow", _SMALL_RECT),
                    ("dwMaximumWindowSize", _COORD)]

    class _KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD),
                    ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", wintypes.WCHAR),
                    ("dwControlKeyState", wintypes.DWORD)]

    class _EVENT_UNION(ctypes.Union):
        _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]

    class _INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT_UNION)]
except Exception:                                  # pragma: no cover
    _COORD = _SMALL_RECT = _CONSOLE_SCREEN_BUFFER_INFO = None
    _KEY_EVENT_RECORD = _EVENT_UNION = _INPUT_RECORD = None

#: Physical keys are read as Win32 virtual-key codes, so a binding fires on the
#: same key whatever the active layout is: pressing the physical D key with the
#: Russian layout yields "в" as a character but still VK_D here.
_CYRILLIC_TO_LATIN = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.")
_CYRILLIC_TO_LATIN_UPPER = str.maketrans(
    "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ",
    "QWERTYUIOP[]ASDFGHJKL;'ZXCVBNM,.")
_VK_KEYS = {0x09: "tab", 0x0D: "enter", 0x1B: "escape", 0x20: " ",
            0x25: "left", 0x27: "right", 0x26: "up", 0x28: "down",
            0x08: "backspace", 0x2E: "delete"}
_NO_CONSOLE = object()
_WIN_INPUT = None


def _layout_key(ch: str) -> str:
    """Map a Cyrillic character typed on a Latin key back to that Latin key."""
    if not ch:
        return ch
    mapped = ch.translate(_CYRILLIC_TO_LATIN)
    if mapped != ch:
        return mapped
    return ch.translate(_CYRILLIC_TO_LATIN_UPPER)


def _key_from_vk(code: int, shift: bool = False, uchar: str = "") -> Optional[str]:
    """Turn a virtual-key code into the menu's logical key, layout-independently."""
    if 0x41 <= code <= 0x5A:                     # A-Z
        return chr(code) if shift else chr(code + 32)
    if 0x30 <= code <= 0x39:                     # 0-9 and their shifted symbols
        if uchar and uchar.isprintable():
            return uchar
        return chr(code)
    if code == 0xBF:                             # / and ?
        return "?" if shift or uchar == "?" else "/"
    named = _VK_KEYS.get(code)
    if named:
        return named
    if uchar and uchar.isprintable():
        return uchar
    return None


def _win_input_handle():
    if os.name != "nt" or _INPUT_RECORD is None:
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        handle = kernel32.GetStdHandle(-10)     # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        return handle
    except Exception:
        return None


def _read_win_key(timeout: float):
    """A logical key, ``None`` on timeout, or ``_NO_CONSOLE`` when unsupported."""
    global _WIN_INPUT
    if _WIN_INPUT is None:
        _WIN_INPUT = _win_input_handle() or False
    handle = _WIN_INPUT
    if not handle:
        return _NO_CONSOLE
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.ReadConsoleInputW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_INPUT_RECORD), wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD)]
        deadline = time.time() + timeout
        while time.time() < deadline:
            wait_ms = max(0, int((deadline - time.time()) * 1000))
            ready = kernel32.WaitForSingleObject(handle, wait_ms)
            if ready == 0x102:                  # WAIT_TIMEOUT
                return None
            if ready != 0:                      # abandoned/invalid handle
                return _NO_CONSOLE
            records = (_INPUT_RECORD * 32)()
            read = wintypes.DWORD()
            if not kernel32.ReadConsoleInputW(
                    handle, records, 32, ctypes.byref(read)):
                return _NO_CONSOLE
            for index in range(read.value):
                record = records[index]
                if record.EventType != 1:       # KEY_EVENT
                    continue
                event = record.Event.KeyEvent
                if not event.bKeyDown:
                    continue
                shift = bool(event.dwControlKeyState & 0x0010)
                key = _key_from_vk(event.wVirtualKeyCode, shift,
                                   event.uChar or "")
                if key:
                    return key
        return None
    except Exception:
        return _NO_CONSOLE


#: What the installer recorded about the node it registered: ``kind``
#: (service|logon|none), ``mode`` (standalone|tunnel), ``port`` and the Funnel
#: host. ``rhm`` is the surface that tells the operator which address to paste
#: into the MCP client, so it has to describe the *installed* node rather than
#: the per-user leftovers of an earlier install.
AUTOSTART_STATE_FILE = "autostart.state.json"

#: Per-node environment files: a standalone/Funnel node keeps its token and its
#: published name in ``standalone.env``, a tunnel node keeps its gateway
#: credentials in ``agent.env`` and the gateway origin in ``domain.env``.
STANDALONE_ENV_FILE = "standalone.env"
STANDALONE_TOKEN_FILE = "standalone.token"
SERVICE_ENV_FILE = "service.env"

#: Port a standalone node binds when neither the installer state nor the caller
#: names one (kept equal to ``core.server._STANDALONE_PORT``).
DEFAULT_STANDALONE_PORT = 8096


def config_dir() -> str:
    # An explicit MESH_CONFIG_DIR is the node's own word about where its files
    # are (the administrator service passes it). Deciding by "which directory
    # happens to have an agent.env" instead described the tunnel-era install
    # while the running service was a Funnel node: a bridge URL with no tunnel
    # into this machine, which is the bug this whole section exists to prevent.
    if (os.environ.get(paths.CONFIG_DIR_ENV) or "").strip():
        return paths.config_dir()
    base = paths.config_dir()
    legacy = paths.legacy_config_dir()
    if not os.path.exists(os.path.join(base, "agent.env")) and os.path.exists(
        os.path.join(legacy, "agent.env")
    ):
        return legacy
    return base


def _read_env_file(path: str) -> Dict[str, str]:
    """MESH_* values from one env file; an unreadable file is simply empty."""
    try:
        return domain.read_env_file(path)
    except Exception:
        return {}


def read_agent_env(directory: Optional[str] = None) -> Dict[str, str]:
    path = os.path.join(directory or config_dir(), "agent.env")
    return _read_env_file(path)


def _autostart_state(directory: str) -> Dict[str, Any]:
    """The installer's record of what it registered, or {} when there is none."""
    try:
        with open(os.path.join(directory, AUTOSTART_STATE_FILE), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _service_installed(directory: str) -> bool:
    """True when *directory* belongs to a machine-wide service install."""
    if os.path.isfile(os.path.join(directory, SERVICE_ENV_FILE)):
        return True
    return str(_autostart_state(directory).get("kind") or "").strip().lower() == "service"


def install_dir() -> str:
    """Configuration directory of the node this machine actually runs.

    The administrator service keeps everything under ``%ProgramData%\\<app>``
    (``service.env`` names the interpreter and ``MESH_CONFIG_DIR``), so ``rhm``
    in an ordinary terminal has to look there first; otherwise it describes
    whatever an older per-user install left in ``~/.config``.
    """
    if (os.environ.get(paths.CONFIG_DIR_ENV) or "").strip():
        return paths.config_dir()
    service = paths.service_config_dir()
    if _service_installed(service):
        return service
    return config_dir()


def _public_host(value: Any) -> str:
    """A name fit to publish: dotted, no scheme, no loopback and no IP literal."""
    text = str(value or "").strip().strip('"').strip("'")
    if not text or "://" in text or "/" in text or " " in text:
        return ""
    if "." not in text or text.lower() == "localhost":
        return ""
    if text.replace(".", "").isdigit():          # an IPv4 literal
        return ""
    return text


def _allowed_public_host(directory: str) -> str:
    """First valid name in ``MESH_ALLOWED_HOSTS`` (the Funnel host the installer wrote)."""
    values = _read_env_file(os.path.join(directory, STANDALONE_ENV_FILE))
    for part in (values.get("MESH_ALLOWED_HOSTS") or "").split(","):
        host = _public_host(part)
        if host:
            return host
    return ""


def node_mode(directory: str, state: Optional[Dict[str, Any]] = None) -> str:
    """``standalone`` (the node publishes itself) or ``tunnel`` (through a gateway).

    The installer's record decides. Without one, a token in ``standalone.env``
    is the next best evidence, and ``tunnel`` is the default because that is
    what an install with neither file is.
    """
    state = _autostart_state(directory) if state is None else state
    mode = str(state.get("mode") or "").strip().lower()
    if mode in ("standalone", "funnel"):
        return "standalone"
    if mode in ("tunnel", "gateway", "agent"):
        return "tunnel"
    values = _read_env_file(os.path.join(directory, STANDALONE_ENV_FILE))
    if (values.get("MESH_TOKEN") or "").strip():
        return "standalone"
    return "tunnel"


def _standalone_token(directory: str) -> str:
    """The token a standalone node checks, from its env file or its token file."""
    values = _read_env_file(os.path.join(directory, STANDALONE_ENV_FILE))
    token = (values.get("MESH_TOKEN") or "").strip()
    if token:
        return token
    try:
        with open(os.path.join(directory, STANDALONE_TOKEN_FILE), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _standalone_port(state: Dict[str, Any]) -> int:
    """Port the standalone node listens on (the installer records it)."""
    try:
        return int(state.get("port"))
    except (TypeError, ValueError):
        return DEFAULT_STANDALONE_PORT


def _standalone_base(directory: str, state: Dict[str, Any]) -> str:
    """The address a Funnel node is really published at.

    Mirrors ``core.server._standalone_public_url``: the installer's
    ``MESH_PUBLIC_URL`` wins, the Funnel host it recorded is next, the allowlist
    after that, and loopback last. ``domain.env`` is deliberately not consulted:
    a Funnel node never dials the gateway that file names, so a link built from
    it points at a bridge with no tunnel into this machine.
    """
    values = _read_env_file(os.path.join(directory, STANDALONE_ENV_FILE))
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL"):
        base = domain.normalise_public_base_url(values.get(key) or os.environ.get(key))
        if base:
            return base
    host = _public_host(state.get("funnel")) or _allowed_public_host(directory)
    if host:
        return "https://" + host
    port = state.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_STANDALONE_PORT
    return "http://127.0.0.1:%d" % port


def _node_label(state: Dict[str, Any], env: Dict[str, str], directory: str, mode: str) -> str:
    """Short name for the header: the node as the mesh knows it, else this host."""
    if mode != "standalone":
        return (env.get("MESH_USER") or "").strip() or socket.gethostname()
    host = _public_host(state.get("funnel")) or _allowed_public_host(directory)
    if host:
        return host.split(".")[0]
    return socket.gethostname()


def _gateway_public_base(directory: str, env: Dict[str, str]) -> str:
    """Origin of the gateway a tunnel node is reached through."""
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL"):
        base = domain.normalise_public_base_url(os.environ.get(key))
        if base:
            return base
    values = _read_env_file(os.path.join(directory, "domain.env"))
    for key in ("MESH_PUBLIC_URL", "AGY_PUBLIC_BASE_URL"):
        base = domain.normalise_public_base_url(values.get(key))
        if base:
            return base
    raw = (env.get("MESH_GATEWAY") or "").strip()
    if raw:
        return raw if "://" in raw else "https://" + raw
    return domain.public_base_url().rstrip("/")


def node_endpoint(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Address of the installed node, and what to print next to it.

    Returns ``{"mode", "dir", "url", "token", "node", "public_base", "hint"}``.
    A standalone/Funnel node is its own public endpoint: its own ``ts.net`` name
    with the standalone token. A tunnel node keeps the gateway shape
    ``<gateway>/sse?user=...&token=<link key>``. ``hint`` says why a URL is
    missing, so no caller has to guess which file was empty.
    """
    directory = install_dir()
    state = _autostart_state(directory)
    mode = node_mode(directory, state)
    info: Dict[str, Any] = {
        "mode": mode,
        "dir": directory,
        "port": _standalone_port(state),
        "url": "",
        "token": "",
        "node": "",
        "public_base": "",
        "hint": "",
    }
    if mode == "standalone":
        base = _standalone_base(directory, state)
        token = _standalone_token(directory)
        info["public_base"] = base
        info["token"] = token
        info["node"] = _node_label(state, env or {}, directory, mode)
        if base and token:
            info["url"] = "%s/sse?token=%s" % (base.rstrip("/"), token)
        elif not token:
            info["hint"] = ("MCP endpoint: the Funnel node has no token yet (no MESH_TOKEN in %s)."
                            % os.path.join(directory, STANDALONE_ENV_FILE))
        else:
            info["hint"] = "MCP endpoint: the Funnel node has no public address yet."
        return info
    gateway_env = dict(env) if env is not None else read_agent_env(directory)
    base = _gateway_public_base(directory, gateway_env)
    user = (gateway_env.get("MESH_USER") or "").strip()
    link = (gateway_env.get("MESH_LINK_KEY") or "").strip()
    info["public_base"] = base
    info["token"] = link
    info["node"] = _node_label(state, gateway_env, directory, mode)
    if user and link:
        info["url"] = "%s/sse?user=%s&token=%s" % (base, user, link)
    elif not user:
        info["hint"] = "MCP endpoint: (no MESH_USER / MESH_LINK_KEY in agent.env)"
    else:
        info["hint"] = ("MCP endpoint: link key missing for '%s' (gateway unreachable or rotate failed)." % user)
    return info


def build_mcp_url(env: Optional[Dict[str, str]] = None) -> str:
    """The URL to paste into the MCP client, for the installed node."""
    return str(node_endpoint(env).get("url") or "")


def _gateway_base(env: Dict[str, str], directory: Optional[str] = None) -> str:
    """Origin of the gateway this node dials, for the key rotation call."""
    return _gateway_public_base(directory or config_dir(), env)


def _write_env_value(path: str, key: str, value: str) -> None:
    lines = []
    found = False
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith(key + "="):
            lines[lines.index(line)] = "%s=%s" % (key, value)
            found = True
            break
    if not found:
        lines.append("%s=%s" % (key, value))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def ensure_link_key(env: Optional[Dict[str, str]] = None,
                     directory: Optional[str] = None) -> str:
    """Return the link key, rotating one through the gateway for old installs.

    Nodes installed before 0.4.6 have no ``MESH_LINK_KEY`` in ``agent.env``; the
    menu would otherwise show no endpoint at all.  The device token proves
    ownership; the returned key is written 0600 next to the token in the
    configuration directory of the node that is being described.
    """
    target_dir = directory or config_dir()
    env = dict(env or read_agent_env(target_dir))
    link = (env.get("MESH_LINK_KEY") or "").strip()
    if link:
        return link
    user = (env.get("MESH_USER") or "").strip()
    token = (env.get("MESH_TOKEN") or "").strip()
    if not user or not token:
        return ""
    try:
        import urllib.request

        request = urllib.request.Request(
            "%s/v1/devices/%s/rotate" % (_gateway_base(env, target_dir), user),
            method="POST",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        link = str(payload.get("link_key") or "").strip()
        if link:
            _write_env_value(os.path.join(target_dir, "agent.env"), "MESH_LINK_KEY", link)
            env["MESH_LINK_KEY"] = link
        return link
    except Exception:
        return ""


def agent_log_path() -> str:
    """The log of the installed node (the service keeps it in its own directory)."""
    return os.path.join(install_dir(), "agent.log")


def tail(path: str, lines: int = 12) -> List[str]:
    if not os.path.exists(path):
        return ["(log not found: %s)" % path]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            data = handle.readlines()
        return [line.rstrip("\n") for line in data[-max(1, lines):]]
    except OSError as exc:
        return ["(cannot read log: %s)" % exc]


def qr_lines(text: str) -> List[str]:
    """ANSI/Unicode QR, or a clear hint when the optional encoder is absent."""
    if not text:
        return []
    try:
        import qrcode  # type: ignore
    except Exception:
        return ["[QR unavailable: pip install qrcode]"]
    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=1)
        qr.add_data(text)
        qr.make(fit=True)
        buffer = io.StringIO()
        qr.print_ascii(out=buffer, tty=False, invert=True)
        return buffer.getvalue().splitlines()
    except Exception as exc:  # pragma: no cover - optional dependency
        return ["[QR failed: %s]" % exc]


def snapshot() -> Dict[str, Any]:
    env = read_agent_env()
    state = control.state()
    info = node_endpoint(env)
    return {
        "online": node_online(info, state),
        "mode": state["access_mode"],
        "mode_label": state["access_label"],
        "workspace": state["workspace"] or os.getcwd(),
        "url": info["url"],
        "endpoint_mode": info["mode"],
        "node": info["node"],
        "config_dir": info["dir"],
        "log": agent_log_path(),
        "isolation": sandbox.status(),
        "remote_control": state.get("remote_control") or control.remote_control_state(),
        "goal": goal.get() if goal.enabled() else None,
        "goal_enabled": goal.enabled(),
        "usage": usage.summary(days=1),
    }


# ---------------------------------------------------------------------------
# Terminal input
# ---------------------------------------------------------------------------

class Keys:
    """Raw single-key reader for Windows and POSIX terminals."""

    def __init__(self) -> None:
        self._fd = None
        self._settings = None
        self._windows = os.name == "nt"

    def __enter__(self) -> "Keys":
        global _ANSI
        if self._windows:
            _ANSI = self._enable_ansi()
        if _ANSI:
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
        return self
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:
            self._fd = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # The raw-mode setting must be restored on every path, including a
        # Ctrl+C inside the menu: without this method `with Keys()` raised
        # TypeError before the first frame was ever drawn.
        if _ANSI:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        self.restore()
        return False

    def restore(self) -> None:
        if self._fd is not None and self._settings is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._settings)
            except Exception:
                pass
            self._fd = None

    def resume(self) -> None:
        if not self._windows:
            try:
                import tty

                if sys.stdin.isatty():
                    tty.setcbreak(sys.stdin.fileno())
            except Exception:
                pass

    def read(self, timeout: float = 0.5) -> Optional[str]:
        if self._windows:
            # Virtual-key codes first: the same physical key, any layout.
            key = _read_win_key(timeout)
            if key is not _NO_CONSOLE:
                return key
            import msvcrt

            end = time.time() + timeout
            while time.time() < end:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        code = msvcrt.getwch()
                        return {"K": "left", "M": "right", "H": "up", "P": "down"}.get(code)
                    if ch == "\t":
                        return "tab"
                    return _layout_key(ch)
                time.sleep(0.03)
            return None
        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                return None
            ch = os.read(sys.stdin.fileno(), 1).decode("utf-8", "replace")
            if ch == "\x1b":
                seq = ""
                # Read the rest of an arrow sequence without blocking forever.
                while len(seq) < 2 and select.select([sys.stdin], [], [], 0.01)[0]:
                    seq += os.read(sys.stdin.fileno(), 1).decode("utf-8", "replace")
                return {"[D": "left", "[C": "right", "[A": "up", "[B": "down"}.get(seq)
            if ch == "\t":
                return "tab"
            return ch
        except Exception:
            time.sleep(timeout)
            return None

    @staticmethod
    def _enable_ansi() -> bool:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
        except Exception:
            pass
        return False


def _endpoint_info(env: Dict[str, str]) -> Dict[str, Any]:
    """``node_endpoint`` plus the key rotation a tunnel install may still need.

    Old tunnel installs predate ``MESH_LINK_KEY``; rotating it is the only
    action the menu takes here, and it is pointless for a Funnel node, whose
    URL carries the standalone token instead.
    """
    info = node_endpoint(env)
    if info["mode"] == "tunnel" and not (env.get("MESH_LINK_KEY") or "").strip():
        link = ensure_link_key(env, info["dir"])
        if link:
            merged = dict(env)
            merged["MESH_LINK_KEY"] = link
            info = node_endpoint(merged)
    return info


#: How long one health answer is trusted. The menu repaints every second;
#: probing the node on every repaint would make the cursor stutter for no
#: reason, and a node that died is noticed within one interval anyway.
_ALIVE_TTL = 5.0
_ALIVE_CACHE: Dict[int, tuple] = {}


def _probe_health(port: int, timeout: float = 0.6) -> bool:
    """True when the local standalone node answers /health."""
    try:
        import urllib.request

        url = "http://127.0.0.1:%d/health" % int(port)
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except Exception:
        return False


def _standalone_alive(port: int, now: Optional[float] = None) -> bool:
    """``_probe_health`` with a short cache, so a repaint costs nothing."""
    port = int(port)
    now = time.time() if now is None else now
    cached = _ALIVE_CACHE.get(port)
    if cached and now - cached[0] < _ALIVE_TTL:
        return bool(cached[1])
    alive = _probe_health(port)
    _ALIVE_CACHE[port] = (now, alive)
    return alive


def node_online(info: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> bool:
    """Is the installed node alive? Two different proofs, one line in the menu.

    A tunnel node writes ``agent.heartbeat`` and the menu reads it. A
    standalone/Funnel node writes no heartbeat at all (``core.agent`` is the
    tunnel), and it usually runs as a service under another account, so the
    user-side heartbeat file is either missing or stale: the node was called
    OFFLINE while it was answering Gemini. Its own loopback port is the only
    proof that does not depend on which account and directory it runs in.
    """
    state = control.state() if state is None else state
    if state.get("online"):
        return True
    if info.get("mode") == "standalone":
        return _standalone_alive(int(info.get("port") or DEFAULT_STANDALONE_PORT))
    return False


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_lines(env: Dict[str, str], *, include_endpoint: bool = True,
                  include_log: bool = True,
                  columns: Optional[int] = None) -> List[str]:
    env = dict(env)
    info = _endpoint_info(env)
    state = control.state()
    url = info["url"]
    token = info["token"]
    node = info["node"] or env.get("MESH_USER") or "?"
    lines: List[str] = []
    lines.append("Remote Harness MCP  ·  rhm")
    lines.append("=" * 72)
    status = "ONLINE" if node_online(info, state) else "OFFLINE"
    lines.append("Status: %-8s  Node: %-20s  Usage today: %s calls"
                 % (status, node, usage.summary(days=1).get("events", 0)))
    mode = state["access_mode"]
    labels = control.ACCESS_LABELS
    order = control.ACCESS_MODES
    pretty = "   ".join(
        ("[ %s ]" % labels[m]) if m == mode else ("  %s  " % labels[m]) for m in order
    )
    lines.append("Mode:  <  %s  >      (Tab / Left / Right)" % pretty)
    home = state["workspace"] or os.getcwd()
    home_button = "  [d] edit"
    if columns:
        # Keep the button visible: shrink the path, never the affordance.
        room = max(8, columns - len("Home:  ") - len(home_button))
        home = _fit(home, room)
    lines.append("Home:  %s%s" % (home, home_button))
    isolated = sandbox.status()
    if isolated["enabled"]:
        lines.append(
            "Isolation: ON  ·  %s  ·  %s  ·  network=%s  ·  %s"
            % (
                isolated["workspace"],
                isolated["container"],
                "on" if isolated.get("network") else "off",
                isolated.get("secure_runtime") or "docker-default",
            )
        )
    else:
        lines.append("Isolation: OFF  (i strict · I with network — Docker/Podman)")
    lock = state.get("remote_control") or {}
    if lock.get("enabled", True):
        lines.append("Remote control: ON  (cabinet/MCP may change the switches; r — turn off)")
    else:
        lines.append("Remote control: OFF  (r — turn on; only this console can re-enable it)")
    if goal.enabled():
        lines.append(goal.summary_line())
    try:
        summary = schedule_module.summary()
    except Exception:
        summary = {}
    if summary.get("active") or summary.get("pending_continuations"):
        lines.append("Schedules: %d active · %d fired unacknowledged%s  (rhm schedule)"
                     % (summary.get("active", 0), summary.get("pending_continuations", 0),
                        (" · next %s" % summary["next_due"]) if summary.get("next_due") else ""))
    if _NOTICE:
        lines.append("Note:  %s" % _NOTICE)
    lines.append("")
    if include_endpoint:
        if url:
            lines.append("MCP endpoint:")
            lines.append(url)
            if token:
                lines.append("Token: %s" % token)
            qr = qr_lines(url)
            if qr:
                lines.append("")
                lines.extend(qr)
        else:
            lines.append(info["hint"] or "MCP endpoint: not configured")
            if info["mode"] == "tunnel" and env.get("MESH_USER"):
                lines.append("Run the installer again, or POST /v1/devices/%s/rotate with the device token."
                             % env.get("MESH_USER"))
    lines.append("")
    if include_log:
        lines.append("Agent log (%s):" % agent_log_path())
        lines.extend("  " + line for line in tail(agent_log_path(), 10))
        lines.append("")
    lines.append("-" * 72)
    lines.append("q quit   ? help   e QR/endpoint   o log")
    if columns:
        lines = [_fit(line, columns) for line in lines]
    return lines

def render(env: Dict[str, str]) -> str:
    """Full menu text: ``--once`` and scripts get everything, no terminal fit."""
    return "\n".join(_render_lines(env))


def _terminal_size() -> tuple:
    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(20, size.columns), max(8, size.lines)


def _fit(text: str, columns: int) -> str:
    """One display line, never wider than the terminal."""
    if columns <= 1 or len(text) <= columns:
        return text[:columns]
    return text[:max(0, columns - 1)] + "\u2026"


def _frame(env: Dict[str, str], columns: int, rows: int) -> str:
    """The interactive frame, fitted to the window, with the live key and QR.

    The QR is placed beside the status block when the window is wide enough, or
    under it when it is tall enough; if neither fits, the frame says which key
    opens it full-screen. The token and URL are always visible.
    """
    env = dict(env)
    info = _endpoint_info(env)
    base = _render_lines(env, include_endpoint=False, include_log=False, columns=columns)
    lines_ok = len(base) >= 2
    hint = base[-1] if base else ""
    footer = base[-2:] if lines_ok else list(base)
    body = base[:-2] if lines_ok else []

    url = info["url"]
    token = info["token"]
    endpoint = ["", "MCP endpoint:"]
    if url:
        endpoint.append(_fit(url, columns))
    else:
        endpoint.append(_fit(info["hint"] or "not configured", columns))
    if token:
        endpoint.append("Token: %s" % _fit(token, max(8, columns - 7)))
    qr = qr_lines(url) if url else []
    qr_width = max((len(line) for line in qr), default=0)

    def assemble(composed: List[str]) -> str:
        if len(composed) > rows:
            # Keep the footer (separator + key hint) no matter how small the
            # window is: without it the menu gives no way to quit.
            footer_keep = composed[-2:] if len(composed) >= 2 else composed
            room = max(0, rows - len(footer_keep) - 1)
            composed = composed[:room] + [
                "\u2026 window is %dx%d: resize it, or run `rhm status`" % (columns, rows)
            ] + footer_keep
        return "\n".join(_fit(line, columns) for line in composed)

    left_width = max((len(line) for line in body), default=0)
    if qr and columns >= left_width + qr_width + 4 \
            and rows >= max(len(body), len(qr)) + len(endpoint) + 2:
        merged = []
        for index in range(max(len(body), len(qr))):
            left = body[index] if index < len(body) else ""
            right = qr[index] if index < len(qr) else ""
            merged.append(left.ljust(left_width) + "  " + right)
        return assemble(merged + endpoint + footer)

    if qr and columns >= qr_width \
            and rows >= len(body) + len(endpoint) + len(qr) + 2:
        return assemble(body + endpoint + [""] + qr + footer)

    if qr:
        endpoint.append("QR: press e (window is too small to show it here)")
    return assemble(body + endpoint + footer)


def _win_console() -> bool:
    """True when this stdout is a Win32 console buffer we can repaint directly."""
    if os.name != "nt" or _CONSOLE_SCREEN_BUFFER_INFO is None:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        info = _CONSOLE_SCREEN_BUFFER_INFO()
        return bool(kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)))
    except Exception:
        return False


def _paint_win(body: str) -> bool:
    """Repaint through the console API: no ANSI, no reliance on `cls`.

    Windows 10 consoles only honour ANSI when VT processing is enabled, and some
    launchers hand the process a handle where ``cls``/``\\x1b[2J`` does nothing.
    Clearing the buffer and homing the cursor here works on plain conhost too.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        info = _CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return False
        cells = info.dwSize.X * info.dwSize.Y
        written = wintypes.DWORD()
        origin = _COORD(0, 0)
        kernel32.FillConsoleOutputCharacterW(
            handle, ctypes.c_wchar(" "), cells, origin, ctypes.byref(written))
        kernel32.FillConsoleOutputAttribute(
            handle, info.wAttributes, cells, origin, ctypes.byref(written))
        kernel32.SetConsoleCursorPosition(handle, origin)
        sys.stdout.write(body)
        sys.stdout.flush()
        return True
    except Exception:
        return False


def _paint(body: str) -> None:
    """Show *body* as the whole screen, whatever this terminal supports."""
    global _WIN_CONSOLE
    if os.name == "nt":
        if _WIN_CONSOLE is None:
            _WIN_CONSOLE = _win_console()
        if _WIN_CONSOLE and _paint_win(body):
            return
    if _ANSI:
        sys.stdout.write("\x1b[H" + body + "\x1b[J")
    else:
        os.system("cls" if os.name == "nt" else "clear")
        sys.stdout.write(body)
    sys.stdout.flush()


def draw(env: Dict[str, str], force: bool = False) -> None:
    global _LAST_FRAME
    columns, rows = _terminal_size()
    body = _frame(env, columns, rows)
    if not force and body == _LAST_FRAME:
        return          # identical frame: repainting it only risks duplication
    _LAST_FRAME = body
    _paint(body)


def _show_endpoint(keys: "Keys", env: Dict[str, str]) -> None:
    """Endpoint, token and QR on a screen of their own; any key returns."""
    columns, rows = _terminal_size()
    info = _endpoint_info(dict(env))
    url = info["url"]
    token = info["token"]
    lines = ["MCP endpoint:", ""]
    if url:
        lines.append(url)
        if token:
            lines.append("Token: %s" % token)
        qr = qr_lines(url)
        if qr and max(len(row) for row in qr) <= columns and len(lines) + len(qr) + 3 <= rows:
            lines += [""] + qr
        elif qr:
            lines += ["", "(window too small for the QR code)"]
    else:
        lines.append(info["hint"] or "not configured")
    lines += ["", "press any key to return"]
    body = "\n".join(_fit(line, columns) for line in lines[:rows])
    global _LAST_FRAME
    _LAST_FRAME = None
    _paint(body)
    while not keys.read(0.5):
        pass


def _show_help(keys: "Keys", env: Dict[str, str]) -> None:
    """One screen with every key; any key returns. Keeps the frame short."""
    columns, rows = _terminal_size()
    lines = [
        "keys",
        "",
        "  Tab / Left / Right   access mode (read-only / full)",
        "  d                    working directory",
        "  i                    isolation on/off (strict)",
        "  I                    isolation with network",
        "  r                    remote control on/off",
        "  p                    pause or resume calls",
        "  e                    MCP endpoint and QR code",
        "  o                    open the agent log",
        "  q                    quit",
        "",
        "press any key to return",
    ]
    body = "\n".join(_fit(line, columns) for line in lines[:rows])
    global _LAST_FRAME
    _LAST_FRAME = None
    _paint(body)
    while not keys.read(0.5):
        pass


def _open_log() -> None:
    path = agent_log_path()
    if not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            subprocess.Popen(["notepad.exe", path])
        else:
            subprocess.Popen(["less", "+G", path])
    except Exception:
        pass


def _choose_directory(keys: Keys) -> Optional[str]:
    keys.restore()
    try:
        value = input("\nNew working directory (empty to cancel): ").strip()
    finally:
        keys.resume()
    if not value:
        return None
    result = node_settings.apply({"action": "cd", "path": value}, actor="tui")
    if result.get("error"):
        return "ERROR: %s" % result["error"]
    return str(result.get("message") or "working directory changed")


def run_menu() -> int:
    global _NOTICE
    env = read_agent_env()
    with Keys() as keys:
        draw(env, force=True)
        while True:
            key = keys.read(1.0)
            if key:
                _NOTICE = ""
            if key == "q":
                break
            # Every mutation below goes through core.settings.apply, the same
            # entry point as `rhm` subcommands and the MCP/cabinet tool - so the
            # three surfaces validate identically and write the same audit line.
            if key in ("tab", "right"):
                node_settings.apply(
                    {"action": "mode", "mode": control.next_access_mode()}, actor="tui")
            elif key == "left":
                current = control.access_mode()
                index = (control.ACCESS_MODES.index(current) - 1) % len(control.ACCESS_MODES)
                node_settings.apply(
                    {"action": "mode", "mode": control.ACCESS_MODES[index]}, actor="tui")
            elif key == "d":
                chosen = _choose_directory(keys)
                if chosen and chosen.startswith("ERROR"):
                    _NOTICE = chosen
            elif key == "i":
                if sandbox.status()["enabled"]:
                    result = node_settings.apply({"action": "sandbox", "enabled": False},
                                                 actor="tui")
                else:
                    target = control.workspace() or os.getcwd()
                    result = node_settings.apply(
                        {"action": "sandbox", "enabled": True, "workspace": target}, actor="tui")
                _NOTICE = str(result.get("message") or result.get("error") or "")
            elif key == "I":
                if sandbox.status()["enabled"]:
                    result = node_settings.apply({"action": "sandbox", "enabled": False},
                                                 actor="tui")
                else:
                    target = control.workspace() or os.getcwd()
                    keys.restore()
                    try:
                        answer = input(
                            "\nIsolation WITH NETWORK lets the container reach the host/LAN. "
                            "Use it only to install packages. Continue? [y/N] "
                        ).strip().lower()
                    finally:
                        keys.resume()
                    if answer not in ("y", "yes"):
                        result = {"message": "network isolation cancelled"}
                    else:
                        result = node_settings.apply(
                            {"action": "sandbox", "enabled": True, "workspace": target,
                             "network": True}, actor="tui")
                _NOTICE = str(result.get("message") or result.get("error") or "")
            elif key == "r":
                enabled = not bool(control.remote_control_state().get("enabled", True))
                result = node_settings.apply(
                    {"action": "remote_control", "enabled": enabled}, actor="tui")
                _NOTICE = str(result.get("message") or result.get("error") or "")
            elif key == "p":
                action = "resume" if control.paused() else "pause"
                result = node_settings.apply({"action": action}, actor="tui")
                _NOTICE = str(result.get("message") or result.get("error") or "")
            elif key == "o":
                _open_log()
            elif key == "e":
                _show_endpoint(keys, env)
            elif key == "?":
                _show_help(keys, env)
            # Input prompts and external viewers leave their own text on screen,
            # so those keys force a repaint even when the frame is unchanged.
            draw(env, force=key in ("d", "I", "o", "e", "?"))
    sys.stdout.write("\n")
    return 0


COMMANDS = ("status", "mode", "cd", "isolation", "remote-control", "pause", "resume",
            "goal", "schedule")

_TRUE = ("on", "true", "1", "yes")
_FALSE = ("off", "false", "0", "no")


def _status_text() -> str:
    data = snapshot()
    isolation = data.get("isolation") or {}
    lock = data.get("remote_control") or {}
    lines = [
        "mode:        %s" % data.get("mode"),
        "workspace:   %s" % data.get("workspace"),
        "paused:      %s" % ("yes" if control.paused() else "no"),
        "isolation:   %s" % ("ON at %s" % isolation.get("workspace")
                             if isolation.get("enabled") else "off"),
        "remote:      %s" % ("on" if lock.get("enabled", True) else "off"),
        "schedules:   %s" % _schedule_line(),
        "online:      %s" % ("yes" if data.get("online") else "no"),
    ]
    return "\n".join(lines)


def _schedule_line() -> str:
    try:
        data = schedule_module.summary()
    except Exception:
        return "?"
    if not data.get("active") and not data.get("pending_continuations"):
        return "none"
    text = "%d active" % data.get("active", 0)
    if data.get("next_due_in_sec") is not None:
        text += ", next in %ds" % data["next_due_in_sec"]
    if data.get("pending_continuations"):
        text += ", %d fired (unacknowledged)" % data["pending_continuations"]
    return text


def _run_schedule(value: str, schedule_id: str, as_json: bool) -> int:
    """``rhm schedule`` list; ``cancel <id>`` / ``ack [id]`` for the operator."""
    word = value.strip().lower()
    if word in ("", "list", "show"):
        data = schedule_module.list_all()
        data["summary"] = schedule_module.summary()
    elif word == "cancel":
        if not schedule_id:
            print("rhm: schedule cancel needs an id (see `rhm schedule`)", file=sys.stderr)
            return 2
        if not schedule_module.cancel(schedule_id):
            print("rhm: no active timer with id %s" % schedule_id, file=sys.stderr)
            return 2
        data = schedule_module.list_all()
    elif word in ("ack", "clear"):
        schedule_module.ack(schedule_id)
        data = schedule_module.list_all()
    else:
        print("rhm: schedule needs list, cancel <id> or ack [id]", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    summary = schedule_module.summary()
    print("schedules: %d active, %d fired unacknowledged%s"
          % (summary["active"], summary["pending_continuations"],
             (", next at " + summary["next_due"]) if summary.get("next_due") else ""))
    for item in data.get("items") or []:
        print("  [%s] %-28s %-5s next=%s%s"
              % (item.get("id"), item.get("label"), item.get("status"),
                 item.get("next_due_iso") or "-",
                 (" cmd=" + str(item.get("command"))[:50]) if item.get("command") else ""))
    for item in data.get("continuations") or []:
        print("  FIRED %s (%s): %s%s"
              % (item.get("label"), item.get("fired_at_iso"),
                 item.get("result") or "",
                 (" job=" + item["job_id"]) if item.get("job_id") else ""))
    return 0


def _run_command(args: Any) -> int:
    """One-shot subcommands; the same mutations as the menu, over the same service."""
    command = str(args.command or "")
    value = str(args.value or "").strip()

    if command == "status":
        if args.json:
            print(json.dumps(snapshot(), ensure_ascii=False, default=str))
        else:
            print(_status_text())
        return 0

    if command == "goal":
        return _run_goal(value, str(getattr(args, "text", "") or "").strip(), args.json)

    if command == "schedule":
        return _run_schedule(value, str(getattr(args, "text", "") or "").strip(), args.json)

    payload: Dict[str, Any]
    if command == "mode":
        if value == "approval":
            # Legacy name from scripts and trays: the node no longer prompts, so
            # it means full access instead of failing the command.
            value = "full"
        if value not in control.ACCESS_MODES:
            print("rhm: mode must be one of %s" % ", ".join(control.ACCESS_MODES),
                  file=sys.stderr)
            return 2
        payload = {"action": "mode", "mode": value}
    elif command == "cd":
        if not value:
            print("rhm: cd needs a directory", file=sys.stderr)
            return 2
        payload = {"action": "cd", "path": value}
    elif command == "isolation":
        lowered = value.lower()
        if lowered not in _TRUE + _FALSE:
            print("rhm: isolation needs on or off", file=sys.stderr)
            return 2
        payload = {"action": "sandbox", "enabled": lowered in _TRUE}
    elif command == "remote-control":
        lowered = value.lower()
        if lowered not in _TRUE + _FALSE:
            print("rhm: remote-control needs on or off", file=sys.stderr)
            return 2
        payload = {"action": "remote_control", "enabled": lowered in _TRUE}
    else:                                   # pause / resume
        payload = {"action": command}

    result = node_settings.apply(payload, actor="cli")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        if result.get("error"):
            print("rhm: %s" % result["error"], file=sys.stderr)
        else:
            print(result.get("message") or "ok")
    return 0 if not result.get("error") else 2


def _run_goal(value: str, text: str, as_json: bool) -> int:
    """``rhm goal`` show; ``rhm goal <text>`` set; ``done``/``blocked``/``clear``."""
    word = value.strip().lower()
    if not goal.enabled():
        if word in ("clear", "reset", "forget"):
            goal.clear(actor="cli")
            print("goal cleared (the feature is disabled)")
            return 0
        print("rhm: goal is disabled: one objective would be shared by every chat. "
              "Use `rhm goal clear` to delete a stored one, or set MESH_GOAL=1.",
              file=sys.stderr)
        return 2
    try:
        if not value:
            data = goal.get()
        elif word in ("set", "add", "start"):
            if not text:
                print("rhm: goal set needs the objective text", file=sys.stderr)
                return 2
            data = goal.set_goal(text, actor="cli")
        elif word in ("done", "complete", "finish"):
            data = goal.finish("done", actor="cli", note=text)
        elif word == "blocked":
            data = goal.finish("blocked", actor="cli", note=text)
        elif word in ("clear", "reset", "forget"):
            data = goal.clear(actor="cli")
        else:
            # Convenience: `rhm goal fix the build` sets the text directly.
            data = goal.set_goal(" ".join(part for part in (value, text) if part), actor="cli")
    except ValueError as exc:
        print("rhm: %s" % exc, file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(data, ensure_ascii=False, default=str))
    elif data.get("goal"):
        current = data["goal"]
        print("goal: %s" % current.get("text"))
        print("status: %s" % current.get("status"))
        if current.get("note"):
            print("note: %s" % current["note"])
    else:
        print(goal.summary_line())
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="rhm", description="Remote Harness MCP console menu")
    parser.add_argument("--json", action="store_true", help="print a snapshot as JSON and exit")
    parser.add_argument("--once", action="store_true", help="print the menu once and exit")
    parser.add_argument("--web", action="store_true", help="serve the local web settings page")
    parser.add_argument("command", nargs="?", default="", choices=[""] + list(COMMANDS),
                        help="one-shot settings command: %s" % ", ".join(COMMANDS))
    parser.add_argument("value", nargs="?", default="",
                        help="mode name, directory, on/off, or goal text for the command")
    parser.add_argument("text", nargs="?", default="",
                        help="goal: the objective (with `goal set`) or the closing note")
    args = parser.parse_args(argv)

    if args.web:
        from core import web_settings

        web_settings.serve()
        return 0
    if args.command:
        return _run_command(args)
    if args.json:
        print(json.dumps(snapshot(), ensure_ascii=False, default=str))
        return 0
    if args.once or not sys.stdin.isatty() or not sys.stdout.isatty():
        # No interactive screen (pipe, redirect, service): print once instead of
        # repainting into a stream that cannot be cleared.
        print(render(read_agent_env()))
        return 0
    try:
        return run_menu()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
