#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``rhm`` - the node's console menu and one-shot settings commands.

Shows the agent log, the MCP endpoint and its QR code, and switches the live
access mode with Tab / Left / Right:

    read_only   observation only
    approval    mutating calls wait for "approve" in this menu
    full        no prompts (the operator's explicit choice)

It also sets the agent's working directory, starts the isolated container and
holds the remote-control lock (``r``).  Everything is local: no network, no
token is printed except the link key that is already in the MCP URL.

The same switches without a terminal, for scripts and for restoring the lock::

    rhm status
    rhm mode read_only|approval|full
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

def config_dir() -> str:
    base = paths.config_dir()
    legacy = paths.legacy_config_dir()
    if not os.path.exists(os.path.join(base, "agent.env")) and os.path.exists(
        os.path.join(legacy, "agent.env")
    ):
        return legacy
    return base


def read_agent_env() -> Dict[str, str]:
    path = os.path.join(config_dir(), "agent.env")
    try:
        return domain.read_env_file(path)
    except Exception:
        return {}


def build_mcp_url(env: Optional[Dict[str, str]] = None) -> str:
    env = env or read_agent_env()
    user = (env.get("MESH_USER") or "").strip()
    link = (env.get("MESH_LINK_KEY") or "").strip()
    if not user or not link:
        return ""
    base = domain.public_base_url().rstrip("/")
    return "%s/sse?user=%s&token=%s" % (base, user, link)


def _gateway_base(env: Dict[str, str]) -> str:
    raw = (env.get("MESH_GATEWAY") or "").strip()
    if not raw:
        return domain.public_base_url().rstrip("/")
    if "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/")


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


def ensure_link_key(env: Optional[Dict[str, str]] = None) -> str:
    """Return the link key, rotating one through the gateway for old installs.

    Nodes installed before 0.4.6 have no ``MESH_LINK_KEY`` in ``agent.env``; the
    menu would otherwise show no endpoint at all.  The device token proves
    ownership; the returned key is written 0600 next to the token.
    """
    env = dict(env or read_agent_env())
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
            "%s/v1/devices/%s/rotate" % (_gateway_base(env), user),
            method="POST",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        link = str(payload.get("link_key") or "").strip()
        if link:
            _write_env_value(os.path.join(config_dir(), "agent.env"), "MESH_LINK_KEY", link)
            env["MESH_LINK_KEY"] = link
        return link
    except Exception:
        return ""


def agent_log_path() -> str:
    return os.path.join(config_dir(), "agent.log")


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
    return {
        "online": state["online"],
        "mode": state["access_mode"],
        "mode_label": state["access_label"],
        "workspace": state["workspace"] or os.getcwd(),
        "url": build_mcp_url(env),
        "log": agent_log_path(),
        "pending": control.pending_approvals(),
        "isolation": sandbox.status(),
        "remote_control": state.get("remote_control") or control.remote_control_state(),
        "goal": goal.get(),
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
        if self._windows:
            self._enable_ansi()
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
                    return ch
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
    def _enable_ansi() -> None:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(env: Dict[str, str]) -> str:
    env = dict(env)
    if not env.get("MESH_LINK_KEY"):
        link = ensure_link_key(env)
        if link:
            env["MESH_LINK_KEY"] = link
    state = control.state()
    url = build_mcp_url(env)
    lines: List[str] = []
    lines.append("Remote Harness MCP  ·  rhm")
    lines.append("=" * 72)
    status = "ONLINE" if state["online"] else "OFFLINE"
    lines.append("Status: %-8s  Node: %-20s  Usage today: %s calls"
                 % (status, env.get("MESH_USER", "?"), usage.summary(days=1).get("events", 0)))
    mode = state["access_mode"]
    labels = control.ACCESS_LABELS
    order = control.ACCESS_MODES
    pretty = "   ".join(
        ("[ %s ]" % labels[m]) if m == mode else ("  %s  " % labels[m]) for m in order
    )
    lines.append("Mode:  <  %s  >      (Tab / Left / Right)" % pretty)
    lines.append("Home:  %s" % (state["workspace"] or os.getcwd()))
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
    lines.append(goal.summary_line())
    try:
        summary = schedule_module.summary()
    except Exception:
        summary = {}
    if summary.get("active") or summary.get("pending_continuations"):
        lines.append("Schedules: %d active · %d fired unacknowledged%s  (rhm schedule)"
                     % (summary.get("active", 0), summary.get("pending_continuations", 0),
                        (" · next %s" % summary["next_due"]) if summary.get("next_due") else ""))
    grant = state.get("approval_grant") or {}
    pending = control.pending_approvals()
    if grant.get("active"):
        if grant.get("expires_in"):
            lines.append("Approvals: ALL allowed for another %d min  (R — revoke)"
                         % max(1, int(grant["expires_in"]) // 60))
        else:
            lines.append("Approvals: ALL allowed until revoked  (R — revoke)")
    elif pending:
        lines.append("Approvals: %d waiting — a approve once, A always, x deny"
                     % len(pending))
    else:
        lines.append("Approvals: asked on every mutating call  (a once, A always)")
    if _NOTICE:
        lines.append("Note:  %s" % _NOTICE)
    lines.append("")
    if url:
        lines.append("MCP endpoint:")
        lines.append(url)
        qr = qr_lines(url)
        if qr:
            lines.append("")
            lines.extend(qr)
    else:
        if env.get("MESH_USER"):
            lines.append("MCP endpoint: link key missing for '%s' (gateway unreachable or rotate failed)."
                         % env.get("MESH_USER"))
            lines.append("Run the installer again, or POST /v1/devices/%s/rotate with the device token."
                         % env.get("MESH_USER"))
        else:
            lines.append("MCP endpoint: (no MESH_USER / MESH_LINK_KEY in agent.env)")
    pending = control.pending_approvals()
    if pending:
        lines.append("")
        lines.append("Pending approval (a=approve, x=deny):")
        for item in pending[-3:]:
            lines.append("  %s  %-14s  %s" % (item.get("id"), item.get("tool"), item.get("summary", "")))
    lines.append("")
    lines.append("Agent log (%s):" % agent_log_path())
    lines.extend("  " + line for line in tail(agent_log_path(), 10))
    lines.append("")
    lines.append("-" * 72)
    lines.append("Tab/Left/Right mode   d directory   i isolation   I network   r remote   a approve   A always   R revoke   x deny   p pause   o log   q quit")
    return "\n".join(lines)


def draw(env: Dict[str, str]) -> None:
    sys.stdout.write("\x1b[H" + render(env) + "\x1b[J")
    sys.stdout.flush()


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
        draw(env)
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
            elif key in ("a", "x", "A", "R"):
                pending = control.pending_approvals()
                if key == "A":
                    control.approve_all(0)
                    _NOTICE = ("all mutating calls are allowed until revoked "
                               "(R) or the access mode changes")
                elif key == "R":
                    control.revoke_grant()
                    _NOTICE = "standing approval revoked: mutating calls ask again"
                elif pending and key == "a":
                    control.approve(pending[-1]["id"])
                    _NOTICE = "approved once: the waiting call continues"
                elif pending and key == "x":
                    control.deny(pending[-1]["id"])
                    _NOTICE = "request denied"
            elif key == "p":
                action = "resume" if control.paused() else "pause"
                result = node_settings.apply({"action": action}, actor="tui")
                _NOTICE = str(result.get("message") or result.get("error") or "")
            elif key == "o":
                _open_log()
            draw(env)
    sys.stdout.write("\n")
    return 0


COMMANDS = ("status", "mode", "cd", "isolation", "remote-control", "pause", "resume",
            "goal", "approvals", "schedule")

_TRUE = ("on", "true", "1", "yes")
_FALSE = ("off", "false", "0", "no")


def _status_text() -> str:
    data = snapshot()
    isolation = data.get("isolation") or {}
    lock = data.get("remote_control") or {}
    grant = data.get("approval_grant") or {}
    if grant.get("active"):
        approvals = ("ALL allowed" +
                     (" for %d min" % max(1, int(grant.get("expires_in") or 0) // 60)
                      if grant.get("expires_in") else " until revoked"))
    else:
        approvals = "%d pending / ask each call" % len(data.get("pending") or [])
    lines = [
        "mode:        %s" % data.get("mode"),
        "workspace:   %s" % data.get("workspace"),
        "paused:      %s" % ("yes" if control.paused() else "no"),
        "isolation:   %s" % ("ON at %s" % isolation.get("workspace")
                             if isolation.get("enabled") else "off"),
        "remote:      %s" % ("on" if lock.get("enabled", True) else "off"),
        "approvals:   %s" % approvals,
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


def _run_approvals(value: str, text: str, as_json: bool) -> int:
    """``rhm approvals`` show; ``allow [minutes]`` / ``once`` / ``off`` / ``status``."""
    word = value.strip().lower()
    if word in ("", "status", "show"):
        data = control.grant_status()
        data["pending"] = control.pending_approvals()
    elif word in ("allow", "always", "all", "on"):
        minutes = 0
        try:
            minutes = int(text) if text else 0
        except ValueError:
            minutes = 0
        control.approve_all(minutes * 60 if minutes > 0 else 0)
        data = control.grant_status()
    elif word == "once":
        pending = control.pending_approvals()
        if not pending:
            print("rhm: nothing is waiting for approval", file=sys.stderr)
            return 2
        control.approve(pending[-1]["id"])
        data = control.grant_status()
    elif word in ("off", "revoke", "reset", "none"):
        control.revoke_grant()
        data = control.grant_status()
    else:
        print("rhm: approvals needs show, allow [minutes], once or off", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(data, ensure_ascii=False, default=str))
    elif data.get("active"):
        if data.get("expires_in"):
            print("approvals: ALL allowed for another %d min (rhm approvals off — revoke)"
                  % max(1, int(data["expires_in"]) // 60))
        else:
            print("approvals: ALL allowed until revoked (rhm approvals off)")
    else:
        print("approvals: ask on every mutating call (%d pending)"
              % len(data.get("pending") or []))
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

    if command == "approvals":
        return _run_approvals(value, str(getattr(args, "text", "") or "").strip(), args.json)

    if command == "schedule":
        return _run_schedule(value, str(getattr(args, "text", "") or "").strip(), args.json)

    payload: Dict[str, Any]
    if command == "mode":
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
    if args.once or not sys.stdin.isatty():
        print(render(read_agent_env()))
        return 0
    try:
        return run_menu()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
