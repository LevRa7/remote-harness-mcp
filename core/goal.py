#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/goal.py - one persistent objective for this node, set from the chat.

An MCP client cannot keep a model working between sessions: when the chat is
closed (or the tunnel drops and the client re-initializes), the next turn starts
with no memory of what was being done. The node can hold that memory instead:

* ``goal`` tool - the model (or the person in the chat) records the objective it
  is working on: ``set``, ``get``, ``complete``, ``blocked`` or ``clear``;
* the objective is stored in ``<ConfigDir>/goal.json``, so it survives an agent
  restart and a new chat session;
* :func:`brief` is injected into the gateway's ``initialize`` instructions and
  into ``mesh_status``, so a reconnecting client resumes the same objective
  instead of asking "what were we doing?".

It is a note, not a command: writing it touches no project file and runs
nothing, which is why it keeps working in read-only mode.

Public interface::

    get() -> dict
    set_goal(text, actor, note) -> dict
    finish(status, actor, note) -> dict
    clear(actor) -> dict
    brief() -> str
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional

from core import control

GOAL_FILE = "goal.json"
#: How many closed objectives are kept for context (newest first).
MAX_HISTORY = 20
STATUSES = ("active", "done", "blocked", "replaced")


def _path() -> str:
    return os.path.join(control.control_dir(), GOAL_FILE)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read() -> Dict[str, Any]:
    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {"goal": None, "history": []}
    if not isinstance(payload, dict):
        return {"goal": None, "history": []}
    goal = payload.get("goal")
    history = payload.get("history")
    return {
        "goal": goal if isinstance(goal, dict) else None,
        "history": history if isinstance(history, list) else [],
    }


def _write(payload: Dict[str, Any]) -> None:
    directory = control.control_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    target = _path()
    fd, temporary = tempfile.mkstemp(prefix=".goal-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def get() -> Dict[str, Any]:
    """The current objective (or ``None``) plus the closed ones, newest first."""
    payload = _read()
    goal = payload["goal"]
    history: List[Dict[str, Any]] = payload["history"]
    return {
        "goal": goal,
        "history": history[:MAX_HISTORY],
        "active": bool(goal and goal.get("status") == "active"),
    }


def set_goal(text: str, actor: str = "mcp", note: str = "") -> Dict[str, Any]:
    """Record (or replace) the active objective; never raises on bad input."""
    clean = " ".join(str(text or "").split())
    if not clean:
        raise ValueError("goal text is required")
    payload = _read()
    previous = payload["goal"]
    history = list(payload["history"])
    if previous is not None:
        closed = dict(previous)
        closed["status"] = "replaced" if closed.get("status") == "active" else closed.get("status")
        closed["updated_at"] = _now()
        history.insert(0, closed)
    goal = {
        "id": uuid.uuid4().hex[:12],
        "text": clean[:4000],
        "status": "active",
        "note": " ".join(str(note or "").split())[:500],
        "actor": str(actor or "mcp")[:12],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write({"goal": goal, "history": history[:MAX_HISTORY]})
    return get()


def finish(status: str, actor: str = "mcp", note: str = "") -> Dict[str, Any]:
    """Close the active objective as ``done`` or ``blocked`` and keep it in history."""
    status = str(status or "").strip().lower()
    if status not in ("done", "blocked"):
        raise ValueError("status must be done or blocked")
    payload = _read()
    goal = payload["goal"]
    if not goal:
        raise ValueError("there is no goal to close")
    if goal.get("status") != "active":
        raise ValueError("the goal is already %s" % goal.get("status"))
    goal = dict(goal)
    goal["status"] = status
    goal["updated_at"] = _now()
    if note:
        goal["note"] = " ".join(str(note).split())[:500]
    history = list(payload["history"])
    history.insert(0, goal)
    _write({"goal": None, "history": history[:MAX_HISTORY]})
    return get()


def clear(actor: str = "mcp") -> Dict[str, Any]:
    """Forget the active objective (it stays in history as ``replaced``)."""
    payload = _read()
    goal = payload["goal"]
    history = list(payload["history"])
    if goal is not None:
        closed = dict(goal)
        closed["status"] = "replaced" if closed.get("status") == "active" else closed.get("status")
        closed["updated_at"] = _now()
        history.insert(0, closed)
    _write({"goal": None, "history": history[:MAX_HISTORY]})
    return get()


def brief() -> str:
    """One line for instructions/menus: the active objective, or ""."""
    goal = get().get("goal")
    if not goal or goal.get("status") != "active":
        return ""
    text = str(goal.get("text") or "").strip()
    if not text:
        return ""
    since = str(goal.get("created_at") or "")
    return "%s (set %s)" % (text, since) if since else text


def summary_line() -> str:
    """The line ``rhm``/``mesh_status`` show; explains how to set one when empty."""
    data = get()
    goal = data.get("goal")
    if goal and goal.get("status") == "active":
        return "Goal: %s" % goal.get("text")
    history = data.get("history") or []
    if history:
        last = history[0]
        return "Goal: none (last: %s - %s)" % (last.get("status"), last.get("text"))
    return "Goal: none (set from the chat with the goal tool, or `rhm goal <text>`)"
