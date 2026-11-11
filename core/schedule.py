#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/schedule.py - timers the model sets on the node ("cron from the chat").

Why the node and not the client: an MCP server cannot start a new turn in the
chat, and a long task outlives the client's ~30 s call budget. So the model
leaves a timer on the node instead of blocking:

* ``schedule(set, after_sec=...)``     one shot in N seconds
* ``schedule(set, every_sec=...)``     repeat every N seconds
* ``schedule(set, at="HH:MM")``        every day at that local time
* with ``command`` the node runs it when the timer fires (as a background job,
  so a long command cannot block the scheduler) and stores the job id;
* with only a ``note`` it is a reminder.

A fired timer becomes a *continuation*: it stays pending until the model
acknowledges it (``schedule(ack)``), appears in ``mesh_status`` and is injected
into the gateway's ``initialize`` instructions, so the next turn continues the
task with the result in hand. That is as close to "the model wakes itself" as an
MCP server can get - the work runs on the node; the chat picks it up on its next
turn.

Public interface::

    add(...) -> dict
    list_all() -> dict
    cancel(id) -> bool
    due(now) -> list
    mark_fired(id, job_id, result) -> dict
    pendings() -> list
    ack(id) -> bool
    summary() -> dict
    brief() -> str
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core import control

SCHEDULE_FILE = "schedules.json"
#: Fired continuations kept for the history (and for a later acknowledgment).
MAX_CONTINUATIONS = 20
KINDS = ("after", "every", "at")


def _path() -> str:
    return os.path.join(control.control_dir(), SCHEDULE_FILE)


def _now() -> float:
    return time.time()


def _iso(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _read() -> Dict[str, Any]:
    try:
        payload = json.load(open(_path(), "r", encoding="utf-8"))
    except Exception:
        return {"items": [], "continuations": []}
    if not isinstance(payload, dict):
        return {"items": [], "continuations": []}
    items = payload.get("items")
    continuations = payload.get("continuations")
    return {
        "items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else [],
        "continuations": ([c for c in continuations if isinstance(c, dict)]
                          if isinstance(continuations, list) else []),
    }


def _write(payload: Dict[str, Any]) -> None:
    directory = control.control_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".schedule-", dir=directory)
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
        os.replace(temporary, _path())
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _parse_at(value: str) -> Optional[float]:
    """Next occurrence of a local ``HH:MM`` (today if still ahead, else tomorrow)."""
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def _next_due(entry: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    now = _now() if now is None else now
    kind = entry.get("kind")
    if kind == "after":
        return float(entry.get("due_at") or 0) or None
    if kind == "every":
        every = int(entry.get("every_sec") or 0)
        if every <= 0:
            return None
        base = float(entry.get("due_at") or now)
        if base > now:
            return base
        missed = int((now - base) // every) + 1
        return base + missed * every
    if kind == "at":
        upcoming = _parse_at(str(entry.get("at") or ""))
        return upcoming if upcoming and upcoming > now else None
    return None


def add(label: str, command: str = "", note: str = "", after_sec: Any = None,
        every_sec: Any = None, at: str = "", actor: str = "mcp") -> Dict[str, Any]:
    """Create a timer; exactly one time specification is required."""
    label = " ".join(str(label or "").split())[:200]
    if not label:
        raise ValueError("label is required")
    command = str(command or "").strip()[:4000]
    note = " ".join(str(note or "").split())[:1000]
    now = _now()
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "label": label,
        "command": command,
        "note": note,
        "status": "active",
        "created_at": now,
        "created_by": str(actor or "mcp")[:12],
        "fired_count": 0,
        "last_fired_at": 0,
        "last_job_id": "",
        "last_result": "",
    }
    if after_sec is not None:
        try:
            delay = float(after_sec)
        except (TypeError, ValueError):
            raise ValueError("after_sec must be a number of seconds")
        if delay <= 0:
            raise ValueError("after_sec must be positive")
        entry.update({"kind": "after", "due_at": now + delay})
    elif every_sec is not None:
        try:
            every = float(every_sec)
        except (TypeError, ValueError):
            raise ValueError("every_sec must be a number of seconds")
        if every < 10:
            raise ValueError("every_sec must be at least 10 seconds")
        entry.update({"kind": "every", "every_sec": int(every), "due_at": now + every})
    elif at:
        upcoming = _parse_at(at)
        if upcoming is None:
            raise ValueError("at must be a local time like 09:30")
        entry.update({"kind": "at", "at": str(at).strip(), "due_at": upcoming})
    else:
        raise ValueError("one of after_sec, every_sec or at is required")

    payload = _read()
    payload["items"].append(entry)
    _write(payload)
    return entry


def list_all() -> Dict[str, Any]:
    payload = _read()
    return {
        "items": [
            dict(item, due_at_iso=_iso(float(item.get("due_at") or 0)),
                 next_due_iso=_iso(_next_due(item)))
            for item in payload["items"]
        ],
        "continuations": payload["continuations"][:MAX_CONTINUATIONS],
    }


def cancel(schedule_id: str) -> bool:
    payload = _read()
    target = str(schedule_id or "").strip()
    found = False
    for item in payload["items"]:
        if item.get("id") == target and item.get("status") == "active":
            item["status"] = "cancelled"
            found = True
    if found:
        _write(payload)
    return found


def due(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Active entries whose next occurrence is now or in the past."""
    now = _now() if now is None else now
    result = []
    for item in _read()["items"]:
        if item.get("status") != "active":
            continue
        upcoming = _next_due(item, now)
        if upcoming is not None and upcoming <= now:
            result.append(dict(item, due_at=upcoming))
    return result


def mark_fired(schedule_id: str, job_id: str = "", result: str = "",
               now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Record one firing: an ``every`` timer rolls forward, the others finish."""
    now = _now() if now is None else now
    payload = _read()
    fired = None
    for item in payload["items"]:
        if item.get("id") != schedule_id or item.get("status") != "active":
            continue
        item["fired_count"] = int(item.get("fired_count") or 0) + 1
        item["last_fired_at"] = now
        item["last_job_id"] = str(job_id or "")
        item["last_result"] = str(result or "")[:500]
        due_at = float(item.get("due_at") or now)
        if item.get("kind") == "every":
            every = int(item.get("every_sec") or 60)
            item["due_at"] = max(due_at, now) + every if due_at >= now else now + every
        elif item.get("kind") == "at":
            item["due_at"] = _parse_at(str(item.get("at") or "")) or (now + 86400)
        else:
            item["status"] = "done"
        fired = dict(item)
        continuation = {
            "id": uuid.uuid4().hex[:12],
            "schedule_id": item["id"],
            "label": item.get("label", ""),
            "note": item.get("note", ""),
            "command": item.get("command", ""),
            "fired_at": now,
            "fired_at_iso": _iso(now),
            "job_id": str(job_id or ""),
            "result": str(result or "")[:500],
        }
        continuations = [continuation] + payload["continuations"]
        payload["continuations"] = continuations[:MAX_CONTINUATIONS]
        break
    if fired is not None:
        _write(payload)
    return fired


def pendings() -> List[Dict[str, Any]]:
    """Fired timers the model has not acknowledged yet, newest first."""
    return _read()["continuations"]


def ack(continuation_id: str = "") -> int:
    """Acknowledge one continuation (or all with an empty id); returns the count."""
    payload = _read()
    target = str(continuation_id or "").strip()
    if target:
        kept = [c for c in payload["continuations"] if c.get("id") != target]
    else:
        kept = []
    removed = len(payload["continuations"]) - len(kept)
    if removed:
        payload["continuations"] = kept
        _write(payload)
    return removed


def summary() -> Dict[str, Any]:
    payload = _read()
    active = [item for item in payload["items"] if item.get("status") == "active"]
    upcoming = sorted((_next_due(item) or 0) for item in active)
    return {
        "active": len(active),
        "pending_continuations": len(payload["continuations"]),
        "next_due": _iso(upcoming[0]) if upcoming else "",
        "next_due_in_sec": max(0, int(upcoming[0] - _now())) if upcoming else None,
    }


def brief(limit: int = 3) -> str:
    """Lines for the session instructions: what fired while the chat was idle."""
    lines = []
    for item in pendings()[:max(1, limit)]:
        label = item.get("label") or "timer"
        when = item.get("fired_at_iso") or ""
        result = item.get("result") or ("reminder" if not item.get("command") else "ran")
        note = (" - " + item["note"]) if item.get("note") else ""
        lines.append("%s (%s): %s%s%s [ack: schedule(action=ack, id=%s)]"
                     % (label, when, result, note,
                        (" job=" + item["job_id"]) if item.get("job_id") else "",
                        item.get("id") or "?"))
    return "\n".join(lines)
