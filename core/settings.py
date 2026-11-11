#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/settings.py - one implementation of the node's own switches.

The console menu (``rhm``), the CLI subcommands and the MCP tool
``node_control`` all change the same four things: the access mode (read-only /
approval / full), the working directory, pausing, the isolated container and the
remote-control lock. When each surface wrote its own marker the three could
disagree - the menu says read-only, the cabinet still shows full, and the tool
refuses for a third reason. This module is the single writer; every caller goes
through :func:`apply` and gets the same answer, the same validation and the same
audit line.

Actors (who asked):

``cli`` / ``tui``  a person at the machine. Local, trusted: may lift the
                   remote-control lock and leave read-only.
``web``            a logged-in person in the cabinet. Remote: may leave
                   read-only (that is the point of the cabinet), cannot
                   re-enable a disabled remote-control lock.
``mcp``            a model over the tunnel. Remote: may throttle the node, never
                   lift read-only, never re-enable the lock.

The node cannot tell ``web`` from ``mcp`` by itself - both arrive over the
gateway tunnel. The gateway is the trust boundary: it stamps the cabinet origin
(``_origin=web``) on calls it makes for the cabinet and strips every underscore
key from a model's arguments, so a model cannot claim to be the cabinet.

Public interface::

    apply(payload, actor) -> dict
    snapshot() -> dict
    requires_approval(payload) -> bool
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from core import control, sandbox

ACTORS = ("cli", "tui", "web", "mcp")
REMOTE_ACTORS = frozenset({"web", "mcp"})

REMOTE_CONTROL_OFF = (
    "remote_control_disabled: remote control is switched off on this node, so "
    "settings cannot be changed over the network. Turn it back on at the machine: "
    "`rhm remote-control on` (or `r` in the rhm console menu)."
)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def _audit(actor: str, action: str, ok: bool, detail: str = "") -> None:
    """Append one line to ``settings.log``; a failure here never breaks a call."""
    try:
        directory = control.control_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, "settings.log")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "actor": str(actor)[:12],
            "action": str(action)[:40],
            "ok": bool(ok),
            "detail": str(detail)[:200],
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def snapshot() -> Dict[str, Any]:
    """Everything a caller needs to render the settings, in one call."""
    state = control.state()
    isolation = sandbox.status()
    return {
        "access_mode": state.get("access_mode"),
        "read_only": state.get("read_only"),
        "paused": state.get("paused"),
        "workspace": state.get("workspace") or os.getcwd(),
        "online": state.get("online"),
        "pending_approvals": state.get("pending_approvals", 0),
        "remote_control": state.get("remote_control") or control.remote_control_state(),
        "sandbox": isolation,
        "modes": list(control.ACCESS_MODES),
    }


def _ok(action: str, actor: str, message: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": True,
        "action": action,
        "actor": actor,
        "message": message,
        "state": snapshot(),
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Approval hints
# ---------------------------------------------------------------------------

def _normalise(payload: Any) -> Dict[str, Any]:
    return dict(payload) if isinstance(payload, dict) else {}


def _action(payload: Dict[str, Any]) -> str:
    return str(payload.get("action") or "status").strip().lower()


def requires_approval(payload: Any) -> bool:
    """True when this call escalates the node past the operator's default.

    Only one action does: leaving the console in charge by switching to full
    access while the node is in ``approval`` mode. Everything else either
    throttles the node or changes where it works, and the operator asked the
    cabinet to be able to do that without a second trip to the machine.
    """
    data = _normalise(payload)
    action = _action(data)
    if action == "sandbox" and data.get("enabled") and data.get("network"):
        # Networked isolation can reach the host/LAN; a remote caller must ask
        # the person at the machine first.
        return True
    return action == "mode" and str(data.get("mode") or "").lower() == "full"


def summary(payload: Any) -> str:
    """One short human line for the approval queue."""
    data = _normalise(payload)
    action = _action(data)
    if action == "mode":
        return "access mode -> %s" % (data.get("mode") or "?")
    if action == "cd":
        return "working directory -> %s" % (data.get("path") or "?")
    if action == "sandbox":
        return "isolation%s -> %s" % (
            " (network)" if data.get("network") else "",
            "on" if data.get("enabled") else "off",
        )
    if action == "remote_control":
        return "remote control -> %s" % ("on" if data.get("enabled") else "off")
    return action


# ---------------------------------------------------------------------------
# The one mutation entry point
# ---------------------------------------------------------------------------

def apply(payload: Any, actor: str = "cli") -> Dict[str, Any]:
    """Apply one settings change; never raises, always answers a dict.

    Every surface (console menu, ``rhm`` subcommands, the MCP tool, the
    cabinet) calls this - so a change either works everywhere or is refused
    everywhere for the same reason.
    """
    data = _normalise(payload)
    actor = str(actor or "cli").strip().lower()
    if actor not in ACTORS:
        actor = "mcp"
    remote = actor in REMOTE_ACTORS
    action = _action(data)

    try:
        result = _apply(data, action, actor, remote)
    except Exception as exc:                      # validation must not 500 a node
        result = {"error": "%s: %s" % (type(exc).__name__, exc)}
    _audit(actor, action, "error" not in result,
           result.get("message") or result.get("error") or "")
    return result


def _apply(data: Dict[str, Any], action: str, actor: str, remote: bool) -> Dict[str, Any]:
    if action not in ("status", "mode", "pause", "resume", "cd", "sandbox", "remote_control"):
        return {"error": "unknown action %r: use status, mode, pause, resume, cd, "
                         "sandbox or remote_control" % action}

    # A disabled lock refuses every change, including "status"?  No: reading the
    # switch is how a cabinet page can show *why* nothing else works, and it
    # changes nothing. Idempotent "off" is allowed too, so a caller that already
    # closed the lock is never told it lacks permission to close it.
    if remote and not control.remote_control_enabled():
        idempotent_off = (action == "remote_control" and data.get("enabled") is False)
        if action != "status" and not idempotent_off:
            return {"error": REMOTE_CONTROL_OFF}

    if action == "status":
        return _ok("status", actor, "settings reported")

    if action == "mode":
        return _mode(data, actor, remote)

    if action in ("pause", "resume"):
        paused = control.set_paused(action == "pause")
        return _ok(action, actor, "node %s" % ("paused" if paused else "resumed"))

    if action == "cd":
        return _cd(data, actor)

    if action == "sandbox":
        return _sandbox(data, actor)

    return _remote_control(data, actor, remote)


def _mode(data: Dict[str, Any], actor: str, remote: bool) -> Dict[str, Any]:
    mode = str(data.get("mode") or "").strip().lower()
    if mode not in control.ACCESS_MODES:
        return {"error": "mode must be one of %r" % (list(control.ACCESS_MODES),)}
    current = control.access_mode()
    # Only a model is held to read-only: it is the untrusted caller. The
    # cabinet is the logged-in owner (the gateway stamps _origin=web and strips
    # every underscore key from a model's arguments), and the console/CLI is the
    # person at the machine.
    if current == "read_only" and mode != "read_only" and actor == "mcp":
        return {"error": "read_only: this node is in read-only mode; a remote caller cannot "
                         "lift it. Change it at the machine (rhm) or in the cabinet."}
    control.set_access_mode(mode)
    return _ok("mode", actor, "access mode is now %s" % mode, mode=mode,
               previous=current)


def _cd(data: Dict[str, Any], actor: str) -> Dict[str, Any]:
    raw = str(data.get("path") or "").strip()
    if not raw:
        return {"error": "path is required"}
    target = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(target):
        return {"error": "not a directory: %s" % raw}
    isolation = control.sandbox_state()
    if isolation:
        workspace = os.path.realpath(str(isolation.get("workspace") or ""))
        if not (target == workspace or target.startswith(workspace.rstrip(os.sep) + os.sep)):
            return {"error": "isolation is on at %s: the working directory cannot leave the "
                             "isolated project (disable isolation first)" % workspace}
    control.set_workspace(target)
    return _ok("cd", actor, "working directory is now %s" % target, path=target)


def _sandbox(data: Dict[str, Any], actor: str) -> Dict[str, Any]:
    enabled = data.get("enabled")
    if enabled is None:
        return {"error": "enabled is required (true starts the container, false stops it)"}
    enabled = bool(enabled)
    network = bool(data.get("network", False))
    current = control.sandbox_state()

    if not enabled:
        sandbox.disable(current)
        return _ok("sandbox", actor, "isolation disabled")

    if control.access_mode() == "read_only":
        return {"error": "read_only: isolation cannot be started while the node is read-only"}

    workspace = str(data.get("workspace") or "").strip()
    if not workspace:
        workspace = str((current or {}).get("workspace") or control.workspace() or os.getcwd())
    workspace = os.path.realpath(os.path.expanduser(workspace))
    if not os.path.isdir(workspace):
        return {"error": "not a directory: %s" % workspace}
    if (current
            and os.path.realpath(str(current.get("workspace") or "")) == workspace
            and bool(current.get("network")) == network):
        return _ok("sandbox", actor, "isolation is already on at %s" % workspace,
                   workspace=workspace)
    if not sandbox.available():
        return {"error": "Docker or Podman is not installed on this node; isolation is "
                         "unavailable"}
    try:
        state = sandbox.enable(workspace, network=network)
    except Exception as exc:
        return {"error": "isolation failed: %s" % exc}
    return _ok("sandbox", actor,
               "isolation is on at %s%s" % (state.get("workspace"),
                                            " (network)" if network else ""),
               workspace=state.get("workspace"), container=state.get("container"),
               network=bool(network))


def _remote_control(data: Dict[str, Any], actor: str, remote: bool) -> Dict[str, Any]:
    enabled = data.get("enabled")
    if enabled is None:
        return {"error": "enabled is required (true/false)"}
    enabled = bool(enabled)
    if enabled and remote:
        return {"error": "remote_control_disabled: enabling remote control is a local action. "
                         "Run `rhm remote-control on` at the machine (or press `r` in the "
                         "rhm console menu)."}
    state = control.set_remote_control(enabled, actor)
    if enabled:
        message = "remote control is ON"
    else:
        message = ("remote control is OFF: it can only be turned back on at the machine "
                   "(`rhm remote-control on`)")
    return _ok("remote_control", actor, message, enabled=state["enabled"])
