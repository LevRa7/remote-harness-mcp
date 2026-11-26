"""core/fs_search.py - fast, time-budgeted filesystem search (stdlib only).

Replaces ``glob.glob(..., recursive=True)`` and unbounded ``os.walk`` in
``core/mcp_tools.py`` so broad searches on Windows (e.g. ``D:\\`` or
``C:\\Users``) never hit the ~28 s gateway deadline:

* iterative BFS over ``os.scandir`` (finds shallow matches first instead of
  sinking into one deep dependency tree);
* early directory pruning before descending (never enters ``node_modules``,
  ``.git``, ``.venv``, ``.cache``, ``.bun``, ``$RECYCLE.BIN``, ``WinSxS``,
  etc. unless ``include_ignored=True``);
* symlink / NTFS junction cycle protection;
* strict ``time_budget_ms`` wall-clock ceiling with honest ``partial=True``
  returns and traversal telemetry instead of gateway timeouts.
"""

from __future__ import annotations

from collections import deque
import fnmatch
import os
import re
import time
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

IS_WINDOWS = os.name == "nt"

#: Hard ceiling for glob_find result sets.
GLOB_MAX_RESULTS = 10000
#: Default wall-clock budget (leaves generous headroom inside the 28 s gateway).
DEFAULT_TIME_BUDGET_MS = 12000
MAX_TIME_BUDGET_MS = 20000
MIN_TIME_BUDGET_MS = 50
#: Default per-file size cap for grep_search (2 MiB) so giant logs/dumps do not stall.
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024

#: Directories pruned before descending unless ``include_ignored=True``.
SKIP_DIRS_DEFAULT: FrozenSet[str] = frozenset((
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".cache",
    ".bun",
    ".npm",
    ".cargo",
    ".gradle",
    "$RECYCLE.BIN",
    "System Volume Information",
    "WinSxS",
    "Config.Msi",
    "Recovery",
))

_SKIP_DIRS_LOWER: FrozenSet[str] = frozenset(d.lower() for d in SKIP_DIRS_DEFAULT)


def _should_skip_dir(name: str) -> bool:
    if IS_WINDOWS:
        return name.lower() in _SKIP_DIRS_LOWER
    return name in SKIP_DIRS_DEFAULT


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < low:
        return low
    if result > high:
        return high
    return result


def _segment_to_regex(segment: str) -> str:
    """Convert one glob path segment (no '/') into a regex that never crosses '/'."""
    out: List[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        i += 1
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            j = i
            if j < n and segment[j] in ("!", "^"):
                j += 1
            if j < n and segment[j] == "]":
                j += 1
            while j < n and segment[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
            else:
                stuff = segment[i:j].replace("\\", r"\\")
                if stuff.startswith("!"):
                    stuff = "^" + stuff[1:]
                elif stuff.startswith("^"):
                    stuff = "\\" + stuff
                out.append("[" + stuff + "]")
                i = j + 1
        else:
            out.append(re.escape(ch))
    return "".join(out)


def compile_rel_glob(pattern: str) -> Tuple[re.Pattern, Optional[int]]:
    """Compile a relative glob pattern (may contain '/' and '**').

    Returns ``(compiled_regex, implied_max_depth)``. When the pattern has no
    ``**`` wildcard, ``implied_max_depth`` is the exact directory depth of the
    target file so the BFS walker does not descend deeper than necessary.
    """
    norm = pattern.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    raw_parts = [p for p in norm.split("/") if p and p != "."]
    # Collapse consecutive '**' segments so '**/**/*.py' behaves like '**/*.py'.
    parts: List[str] = []
    for seg in raw_parts:
        if seg == "**" and parts and parts[-1] == "**":
            continue
        parts.append(seg)

    has_globstar = "**" in parts
    implied_max_depth = None if has_globstar else max(0, len(parts) - 1)

    regex_parts: List[str] = ["^"]
    for idx, seg in enumerate(parts):
        is_last = idx == len(parts) - 1
        if seg == "**":
            if is_last:
                regex_parts.append(".*")
            else:
                regex_parts.append("(?:.+/)?")
        else:
            regex_parts.append(_segment_to_regex(seg))
            if not is_last:
                regex_parts.append("/")
    regex_parts.append("$")
    flags = re.IGNORECASE if IS_WINDOWS else 0
    return re.compile("".join(regex_parts), flags), implied_max_depth


def _is_reparse_or_symlink(entry: "os.DirEntry[str]") -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    if IS_WINDOWS:
        try:
            st = entry.stat(follow_symlinks=False)
            attrs = getattr(st, "st_file_attributes", 0)
            # FILE_ATTRIBUTE_REPARSE_POINT = 0x0400 (covers NTFS junctions)
            if attrs & 0x0400:
                return True
        except OSError:
            return True
    return False


def iter_files_bfs(
    base: str,
    *,
    max_depth: Optional[int] = None,
    include_ignored: bool = False,
    deadline: Optional[float] = None,
    stats: Optional[Dict[str, int]] = None,
) -> Iterator[Tuple[str, str]]:
    """Yield ``(full_path, rel_posix_path)`` for files under *base* in BFS order.

    Updates *stats* in place with ``scanned_dirs``, ``pruned_dirs``,
    ``skipped_errors``, and ``timed_out`` (0 or 1).
    """
    if stats is None:
        stats = {}
    stats.setdefault("scanned_dirs", 0)
    stats.setdefault("pruned_dirs", 0)
    stats.setdefault("skipped_errors", 0)
    stats.setdefault("timed_out", 0)

    if os.path.isfile(base):
        yield base, os.path.basename(base)
        return

    queue: deque = deque([(base, "", 0)])
    checked = 0

    while queue:
        if deadline is not None and time.monotonic() >= deadline:
            stats["timed_out"] = 1
            return

        current_dir, rel_dir, depth = queue.popleft()
        stats["scanned_dirs"] += 1

        try:
            it = os.scandir(current_dir)
        except OSError:
            stats["skipped_errors"] += 1
            continue

        subdirs: List[Tuple[str, str, int]] = []
        try:
            with it:
                for entry in it:
                    checked += 1
                    if deadline is not None and (checked & 63) == 0:
                        if time.monotonic() >= deadline:
                            stats["timed_out"] = 1
                            return
                    name = entry.name
                    rel_path = f"{rel_dir}/{name}" if rel_dir else name
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        stats["skipped_errors"] += 1
                        continue

                    if is_dir:
                        if _is_reparse_or_symlink(entry):
                            stats["pruned_dirs"] += 1
                            continue
                        if not include_ignored and _should_skip_dir(name):
                            stats["pruned_dirs"] += 1
                            continue
                        if max_depth is None or depth < max_depth:
                            subdirs.append((entry.path, rel_path, depth + 1))
                    else:
                        yield entry.path, rel_path
        except OSError:
            stats["skipped_errors"] += 1

        # Preserve deterministic ordering within each level.
        if subdirs:
            subdirs.sort(key=lambda item: item[0])
            queue.extend(subdirs)


def glob_find(
    base: str,
    pattern: str,
    *,
    max_depth: Optional[int] = None,
    max_results: int = GLOB_MAX_RESULTS,
    time_budget_ms: int = DEFAULT_TIME_BUDGET_MS,
    include_ignored: bool = False,
) -> Dict[str, Any]:
    """Find files matching *pattern* under *base* with early prune and time budget."""
    t0 = time.monotonic()
    budget_ms = _clamp_int(time_budget_ms, MIN_TIME_BUDGET_MS, MAX_TIME_BUDGET_MS, DEFAULT_TIME_BUDGET_MS)
    limit = _clamp_int(max_results, 1, GLOB_MAX_RESULTS, GLOB_MAX_RESULTS)
    deadline = t0 + (budget_ms / 1000.0)

    is_path_pattern = ("/" in pattern) or ("\\" in pattern) or ("**" in pattern)
    rel_regex: Optional[re.Pattern] = None
    effective_max_depth = max_depth

    if is_path_pattern:
        rel_regex, implied_depth = compile_rel_glob(pattern)
        if implied_depth is not None:
            if effective_max_depth is None or implied_depth < effective_max_depth:
                effective_max_depth = implied_depth

    stats: Dict[str, int] = {}
    seen = set()
    files: List[str] = []
    truncated = False

    for full_path, rel_path in iter_files_bfs(
        base,
        max_depth=effective_max_depth,
        include_ignored=include_ignored,
        deadline=deadline,
        stats=stats,
    ):
        matched = (
            bool(rel_regex.match(rel_path))
            if rel_regex is not None
            else fnmatch.fnmatch(os.path.basename(full_path), pattern)
        )
        if not matched:
            continue
        real = os.path.realpath(full_path)
        if real in seen:
            continue
        seen.add(real)
        if len(files) >= limit:
            truncated = True
            break
        files.append(real)

    files.sort()
    partial = bool(stats.get("timed_out"))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    result: Dict[str, Any] = {
        "files": files,
        "count": len(files),
        "truncated": truncated,
        "partial": partial,
        "backend": "scandir",
        "elapsed_ms": elapsed_ms,
        "scanned_dirs": stats.get("scanned_dirs", 0),
        "pruned_dirs": stats.get("pruned_dirs", 0),
        "note": "[VERIFIED BY TOOL] paths come from the real filesystem",
    }
    if partial:
        result["next_hint"] = (
            "Search hit time_budget_ms (%d ms); narrow 'path' or pass 'max_depth' for complete results."
            % budget_ms
        )
    return result


_REGEX_META_CHARS: FrozenSet[str] = frozenset(".^$*+?{}[]\\|()")


def _is_literal_regex(pattern: str) -> bool:
    """Return True when *pattern* has no regex metacharacters and can match literally."""
    return bool(pattern) and not any(ch in _REGEX_META_CHARS for ch in pattern)


def _is_probably_binary(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return b"\x00" in handle.read(4096)
    except OSError:
        return True


def format_grep_compact(matches: Sequence[Dict[str, Any]], root_path: str) -> str:
    """Format grep_search matches as compact ``rel/path.py:line: text`` lines.

    Paths are made relative to *root_path* when possible and always use forward
    slashes (``/``). Falls back to normalized forward-slash paths when a match
    cannot be expressed relative to *root_path* (e.g., different drive on Windows).
    """
    if not matches:
        return ""

    root_real = os.path.realpath(root_path) if root_path else ""
    root_is_file = bool(root_real) and os.path.isfile(root_real)
    root_real_cmp = os.path.normcase(root_real) if root_real else ""

    lines: List[str] = []
    for item in matches:
        raw_path = str(item.get("path", ""))
        line_no = item.get("line", 0)
        text = str(item.get("text", ""))

        display_path = raw_path
        if root_real and raw_path:
            if os.path.isabs(raw_path):
                path_real = os.path.realpath(raw_path)
                if root_is_file and os.path.normcase(path_real) == root_real_cmp:
                    display_path = os.path.basename(path_real)
                else:
                    base_for_rel = os.path.dirname(root_real) if root_is_file else root_real
                    try:
                        rel = os.path.relpath(path_real, base_for_rel)
                    except ValueError:
                        rel = raw_path
                    display_path = rel
            else:
                display_path = raw_path

        display_path = display_path.replace("\\", "/")
        while display_path.startswith("./"):
            display_path = display_path[2:]
        lines.append(f"{display_path}:{line_no}: {text}")

    return "\n".join(lines)


def grep_search(
    base: str,
    pattern: str,
    *,
    glob_filter: Optional[str] = None,
    limit: int = 200,
    ignore_case: bool = False,
    fixed: bool = False,
    context: int = 0,
    max_depth: Optional[int] = None,
    time_budget_ms: int = DEFAULT_TIME_BUDGET_MS,
    include_ignored: bool = False,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Dict[str, Any]:
    """Search file contents under *base* with early prune, size cap, and time budget."""
    t0 = time.monotonic()
    budget_ms = _clamp_int(time_budget_ms, MIN_TIME_BUDGET_MS, MAX_TIME_BUDGET_MS, DEFAULT_TIME_BUDGET_MS)
    limit_clamped = _clamp_int(limit, 1, 1000, 200)
    context_clamped = _clamp_int(context, 0, 20, 0)
    deadline = t0 + (budget_ms / 1000.0)

    flags = re.IGNORECASE if ignore_case else 0
    needle = ""
    regex: Optional[re.Pattern] = None
    if fixed:
        needle = pattern.lower() if ignore_case else pattern
    else:
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return {"error": "invalid regular expression: %s" % exc}

    # Fast bytes pre-filter for case-sensitive literal searches (fixed=True or
    # regex without metacharacters). Skip when pattern contains U+FFFD because
    # invalid UTF-8 bytes decode to U+FFFD under errors="replace".
    needle_bytes: Optional[bytes] = None
    if not ignore_case and pattern and "\ufffd" not in pattern:
        if fixed or _is_literal_regex(pattern):
            needle_bytes = pattern.encode("utf-8")
            if not fixed:
                needle = pattern

    stats: Dict[str, int] = {}
    matches: List[Dict[str, Any]] = []
    truncated = False
    scanned_files = 0
    skipped_large = 0

    for file_path, _rel_path in iter_files_bfs(
        base,
        max_depth=max_depth,
        include_ignored=include_ignored,
        deadline=deadline,
        stats=stats,
    ):
        if time.monotonic() >= deadline:
            stats["timed_out"] = 1
            break
        if glob_filter and not fnmatch.fnmatch(os.path.basename(file_path), glob_filter):
            continue
        try:
            if max_file_bytes > 0 and os.path.getsize(file_path) > max_file_bytes:
                skipped_large += 1
                continue
        except OSError:
            continue

        try:
            with open(file_path, "rb") as handle:
                head = handle.read(4096)
                if b"\x00" in head:
                    continue
                tail = handle.read()
        except OSError:
            continue

        raw_bytes = head + tail if tail else head
        scanned_files += 1
        if needle_bytes is not None and needle_bytes not in raw_bytes:
            continue

        lines = raw_bytes.decode("utf-8", errors="replace").splitlines()
        hit_lines = []
        if fixed or needle_bytes is not None:
            for idx, line in enumerate(lines):
                if needle in (line.lower() if ignore_case else line):
                    hit_lines.append(idx)
        else:
            for idx, line in enumerate(lines):
                if regex.search(line):  # type: ignore[union-attr]
                    hit_lines.append(idx)
        if not hit_lines:
            continue

        emitted = set()
        real_path = os.path.realpath(file_path)
        for idx in hit_lines:
            first = max(0, idx - context_clamped)
            last = min(len(lines) - 1, idx + context_clamped)
            for lineno in range(first, last + 1):
                if lineno in emitted:
                    continue
                if len(matches) >= limit_clamped:
                    truncated = True
                    break
                emitted.add(lineno)
                matches.append({"path": real_path, "line": lineno + 1, "text": lines[lineno]})
            if truncated:
                break
        if truncated:
            break

    partial = bool(stats.get("timed_out"))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    result: Dict[str, Any] = {
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
        "partial": partial,
        "elapsed_ms": elapsed_ms,
        "scanned_files": scanned_files,
        "scanned_dirs": stats.get("scanned_dirs", 0),
        "pruned_dirs": stats.get("pruned_dirs", 0),
        "skipped_large": skipped_large,
        "note": "[VERIFIED BY TOOL] matches come from the real filesystem",
    }
    if partial:
        result["next_hint"] = (
            "Search hit time_budget_ms (%d ms); narrow 'path', add 'glob', or pass 'max_depth'."
            % budget_ms
        )
    return result


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_TIME_BUDGET_MS",
    "GLOB_MAX_RESULTS",
    "MAX_TIME_BUDGET_MS",
    "SKIP_DIRS_DEFAULT",
    "compile_rel_glob",
    "format_grep_compact",
    "glob_find",
    "grep_search",
    "iter_files_bfs",
]
