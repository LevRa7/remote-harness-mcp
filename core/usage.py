"""Local usage journal (Wave 1, USE-01…USE-14 subset).

Every dispatched tool call is recorded as one JSON line.  The journal deliberately
contains **metadata only** - time, tool name, status, duration, agent id - never
arguments, file paths, command text, output or tokens.  That keeps the log safe to
copy into a bug report and keeps the "no payload" promise the cabinet makes.

The file lives in the per-user config directory (``core.paths.config_dir()``) and
is written 0600.  It rotates at 10 MB and keeps five gzipped generations.  A test
can point ``MESH_USAGE_FILE`` at a temporary path; ``MESH_USAGE_LOG=0`` disables
the journal entirely.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("rhm-usage")

MAX_BYTES = 10 * 1024 * 1024
KEEP_GENERATIONS = 5
_LOCK = threading.Lock()


def _enabled() -> bool:
    value = os.environ.get("MESH_USAGE_LOG", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def usage_path() -> str:
    explicit = os.environ.get("MESH_USAGE_FILE", "").strip()
    if explicit:
        return explicit
    from core import paths

    return os.path.join(paths.config_dir(), "usage.jsonl")


def _max_bytes() -> int:
    try:
        return max(1024, int(os.environ.get("MESH_USAGE_MAX_BYTES", MAX_BYTES)))
    except (TypeError, ValueError):
        return MAX_BYTES


def _rotate(path: str) -> None:
    """Rename the active file and gzip it; caller holds ``_LOCK``."""
    if not os.path.exists(path) or os.path.getsize(path) < _max_bytes():
        return
    # Drop the oldest generation, then shift the rest.
    oldest = f"{path}.{KEEP_GENERATIONS}.gz"
    if os.path.exists(oldest):
        try:
            os.remove(oldest)
        except OSError:
            pass
    for index in range(KEEP_GENERATIONS - 1, 0, -1):
        src = f"{path}.{index}.gz"
        dst = f"{path}.{index + 1}.gz"
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    archived = f"{path}.1"
    try:
        os.replace(path, archived)
        with open(archived, "rb") as src, gzip.open(f"{archived}.gz", "wb") as dst:
            dst.write(src.read())
        os.remove(archived)
    except OSError as exc:  # pragma: no cover - disk/permission failure
        logger.warning("usage rotation failed: %s", exc)


def record(
    tool: str,
    status: str,
    duration_ms: int,
    *,
    call_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    error_code: Optional[str] = None,
) -> None:
    """Append one metadata-only event.  Never raises into the tool path."""
    if not _enabled():
        return
    try:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "call_id": call_id or uuid.uuid4().hex,
            "tool": str(tool)[:80],
            "status": status,
            "duration_ms": int(duration_ms),
            "agent_id": agent_id,
            "error_code": error_code,
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = usage_path()
        with _LOCK:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            _rotate(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except Exception as exc:  # pragma: no cover - journal must never break a call
        logger.debug("usage journal write failed: %s", exc)


def _iter_events() -> Iterable[Dict[str, Any]]:
    path = usage_path()
    if not os.path.exists(path):
        return []
    events: List[Dict[str, Any]] = []
    with _LOCK:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        value = json.loads(raw)
                    except Exception:
                        continue
                    if isinstance(value, dict):
                        events.append(value)
        except OSError:
            return []
    return events


def recent(limit: int = 100) -> List[Dict[str, Any]]:
    events = list(_iter_events())
    return events[-max(0, int(limit)):]


def summary(days: int = 7) -> Dict[str, Any]:
    """Aggregates for the tray/CLI: counts, errors, p50/p95 of the window."""
    cutoff = time.time() - max(1, int(days)) * 86400
    events = []
    for event in _iter_events():
        try:
            stamp = datetime.fromisoformat(str(event.get("ts", "")).replace("Z", "+00:00"))
            if stamp.timestamp() >= cutoff:
                events.append(event)
        except Exception:
            continue
    durations = sorted(int(e.get("duration_ms") or 0) for e in events)
    by_tool: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for event in events:
        by_tool[str(event.get("tool"))] = by_tool.get(str(event.get("tool")), 0) + 1
        by_status[str(event.get("status"))] = by_status.get(str(event.get("status")), 0) + 1

    def percentile(ratio: float) -> int:
        if not durations:
            return 0
        index = min(len(durations) - 1, int(len(durations) * ratio))
        return durations[index]

    return {
        "events": len(events),
        "by_status": by_status,
        "by_tool": by_tool,
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
    }


def clear() -> int:
    """Delete the active journal and its rotated generations."""
    removed = 0
    base = usage_path()
    candidates = [base] + [f"{base}.{i}.gz" for i in range(1, KEEP_GENERATIONS + 1)]
    with _LOCK:
        for path in candidates:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return removed
