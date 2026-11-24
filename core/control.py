"""Local command control for the tray (pause / read-only / status).

The tray is deliberately dumb: it flips two marker files in the node's config
directory and reads the heartbeat/usage journal.  The agent checks the markers on
every call, so a toggle takes effect immediately without restarting the service.

* ``agent.paused``        - refuse every tool call with ``node_paused``.
* ``readonly.flag``       - observation only, even if the service was started
                            without ``MESH_READ_ONLY``.
* ``access.mode``         - ``read_only`` / ``full`` (the console
                            menu's three-way switch).
* ``workspace.txt``       - the agent's current working directory.
* ``remote_control.json`` - ``{enabled}``: may the cabinet/model change those
                            switches remotely? On by default; once off, only the
                            console or ``rhm remote-control on`` turns it back on.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from core import paths

ACCESS_MODES = ("read_only", "full")
ACCESS_LABELS = {
    "read_only": "Read-only",
    "full": "Full access",
}


def control_dir() -> str:
    explicit = os.environ.get("MESH_CONTROL_DIR", "").strip()
    if explicit:
        return explicit
    base = paths.config_dir()
    legacy = paths.legacy_config_dir()
    # An upgraded host may have a new directory with only cabinet files while
    # the running agent still lives in the pre-rename directory. Control files
    # must follow the agent, not the newest directory.
    if not os.path.exists(os.path.join(base, "agent.env")) and os.path.exists(
        os.path.join(legacy, "agent.env")
    ):
        return legacy
    return base


def _path(name: str) -> str:
    return os.path.join(control_dir(), name)


def paused() -> bool:
    return os.path.exists(_path("agent.paused"))


def set_paused(value: bool) -> bool:
    path = _path("agent.paused")
    if value:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("paused\n")
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return paused()


def read_only_flag() -> bool:
    return access_mode() == "read_only"


def set_read_only(value: bool) -> bool:
    return set_access_mode("read_only" if value else "full") == "read_only"


def has_explicit_mode() -> bool:
    """True when the operator chose a mode in ``rhm``/the tray.

    An explicit choice is the permission the operator granted; the env default
    (``MESH_READ_ONLY``) must not override it afterwards. Before the first choice
    the env flag is the safe fallback.
    """
    try:
        raw = open(_path("access.mode"), "r", encoding="utf-8").read().strip().lower()
    except OSError:
        return False
    return raw in ACCESS_MODES


def access_mode() -> str:
    """Explicit menu choice first, then the env/legacy fallback."""
    explicit = None
    try:
        explicit = open(_path("access.mode"), "r", encoding="utf-8").read().strip().lower()
    except OSError:
        pass
    if explicit in ACCESS_MODES:
        return explicit
    if os.path.exists(_path("readonly.flag")):
        return "read_only"
    if explicit == "approval":
        # This node used to queue mutating calls for a prompt. Confirmation is
        # the MCP client's job (Gemini Web asks before a tool call); a node-side
        # prompt only dead-ended every chat that could not answer it.
        return "full"
    env = os.environ.get("MESH_ACCESS_MODE", "").strip().lower()
    if env in ACCESS_MODES:
        return env
    if env == "approval":
        return "full"
    if os.environ.get("MESH_READ_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "read_only"
    return "full"


def set_access_mode(mode: str) -> str:
    mode = str(mode or "").strip().lower()
    if mode == "approval":
        # Legacy name kept for old scripts and tray entries: there is no node-side
        # prompt any more, so it means full access.
        mode = "full"
    if mode not in ACCESS_MODES:
        raise ValueError("mode must be one of %r" % (ACCESS_MODES,))
    os.makedirs(control_dir(), mode=0o700, exist_ok=True)
    path = _path("access.mode")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(mode + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # Keep the old marker in sync so existing tray/agent checks keep working.
    flag = _path("readonly.flag")
    if mode == "read_only":
        with open(flag, "w", encoding="utf-8") as handle:
            handle.write("read-only\n")
    elif os.path.exists(flag):
        os.remove(flag)
    return mode


def next_access_mode(mode: Optional[str] = None) -> str:
    current = mode or access_mode()
    return ACCESS_MODES[(ACCESS_MODES.index(current) + 1) % len(ACCESS_MODES)]


def workspace() -> Optional[str]:
    """The live workspace chosen in ``rhm``; ``None`` means "use the default"."""
    try:
        value = open(_path("workspace.txt"), "r", encoding="utf-8").read().strip()
    except OSError:
        return None
    if value and os.path.isdir(value):
        return os.path.abspath(value)
    return None


def set_workspace(path: str) -> str:
    value = os.path.abspath(os.path.expanduser(str(path).strip()))
    if not os.path.isdir(value):
        raise ValueError("not a directory: %s" % path)
    os.makedirs(control_dir(), mode=0o700, exist_ok=True)
    target = _path("workspace.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return value


# ---------------------------------------------------------------------------
# Isolated container profile (development / untrusted code)
# ---------------------------------------------------------------------------

def sandbox_state() -> Optional[Dict[str, Any]]:
    """``{enabled, workspace, runtime, container}`` when isolation is on."""
    path = _path("sandbox.json")
    try:
        payload = json.loads(open(path, "r", encoding="utf-8").read())
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("enabled"):
        return None
    workspace = str(payload.get("workspace") or "")
    if not workspace or not os.path.isdir(workspace):
        return None
    return payload


def set_sandbox_state(payload: Optional[Dict[str, Any]]) -> None:
    path = _path("sandbox.json")
    if not payload:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(control_dir(), mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Remote control of this node's own settings (web cabinet, MCP tool)
# ---------------------------------------------------------------------------
# ``node_control`` can throttle this node (read-only, pause, workspace,
# isolation) from the cabinet or from a model. That is a remote way to change
# the operator's own switches, so it has its own switch - on the node, next to
# the others:
#
#   * ON by default: the cabinet and the model may throttle the node;
#   * OFF can always be set, from anywhere: somebody who notices the control
#     channel being abused must be able to close it in the same call;
#   * ON can only be set locally (``rhm remote-control on`` or the console
#     menu). If a remote caller could re-enable it, turning it off would be a
#     suggestion rather than a lock.
REMOTE_CONTROL_FILE = "remote_control.json"
DEFAULT_REMOTE_CONTROL = True


def _remote_control_path() -> str:
    return _path(REMOTE_CONTROL_FILE)


def remote_control_state() -> Dict[str, Any]:
    """``{enabled, changed_at, changed_by}``; enabled by default."""
    payload: Dict[str, Any] = {}
    try:
        raw = json.loads(open(_remote_control_path(), "r", encoding="utf-8").read())
        if isinstance(raw, dict):
            payload = raw
    except Exception:
        payload = {}
    enabled = payload.get("enabled")
    return {
        "enabled": DEFAULT_REMOTE_CONTROL if not isinstance(enabled, bool) else enabled,
        "changed_at": str(payload.get("changed_at") or ""),
        "changed_by": str(payload.get("changed_by") or ""),
    }


def remote_control_enabled() -> bool:
    return bool(remote_control_state()["enabled"])


def set_remote_control(enabled: bool, actor: str = "cli") -> Dict[str, Any]:
    """Persist the switch and return the new state (never includes secrets)."""
    payload = {
        "enabled": bool(enabled),
        "changed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "changed_by": str(actor or "cli")[:20],
    }
    os.makedirs(control_dir(), mode=0o700, exist_ok=True)
    target = _remote_control_path()
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return remote_control_state()


def _heartbeat_age() -> Optional[int]:
    path = os.path.join(control_dir(), "agent.heartbeat")
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except OSError:
        return None


def state() -> Dict[str, Any]:
    """A small snapshot for the tray/CLI; never includes tokens."""
    age = _heartbeat_age()
    return {
        "paused": paused(),
        "read_only": access_mode() == "read_only",
        "access_mode": access_mode(),
        "access_label": ACCESS_LABELS.get(access_mode(), access_mode()),
        "workspace": workspace(),
        "remote_control": remote_control_state(),
        "heartbeat_age_sec": age,
        "online": age is not None and age <= 60,
        "usage_file": os.environ.get("MESH_USAGE_FILE")
        or os.path.join(control_dir(), "usage.jsonl"),
    }


def toggle_paused() -> bool:
    return set_paused(not paused())


def toggle_read_only() -> bool:
    return set_read_only(not read_only_flag())
