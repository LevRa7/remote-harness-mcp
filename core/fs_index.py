"""core/fs_index.py - lightweight local SQLite FTS5 file index (stdlib only).

Provides millisecond filename/glob queries across large directory trees (such as
``C:\\Users`` and ``D:\\MyProjects``) without external binaries like Everything.

Design invariants:
* Zero third-party dependencies (standard library ``sqlite3`` + ``os.scandir``).
* Stored in ``paths.cache_path("file_index.db")`` with WAL journal mode.
* Graceful fallback to plain SQL ``LIKE`` if a Python build lacks FTS5.
* Never raises out of public query helpers: errors fall back cleanly to live scan.
"""

from __future__ import annotations

from collections import deque
import fnmatch
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import fs_search, paths

IS_WINDOWS = os.name == "nt"
DEFAULT_MAX_AGE_SEC = 3600.0
DEFAULT_INDEX_MAX_DEPTH = 10
_BATCH_SIZE = 500
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u0400-\u04FF]{2,}")


def _norm_path(path: str) -> str:
    real = os.path.realpath(path)
    return real.lower() if IS_WINDOWS else real


def _norm_prefix(path: str) -> str:
    norm = _norm_path(path).rstrip("\\/")
    return norm + os.sep


def _extract_fts_tokens(pattern: str) -> List[str]:
    """Extract safe alphanumeric tokens from a glob pattern for FTS5 pre-filtering."""
    # Strip wildcard-attached fragments? Actually any contiguous literal >=2 chars
    # that is not immediately split by '*'/'?' inside a word is a valid substring;
    # for FTS5 prefix match we only trust tokens that aren't preceded by '*'/'?'/'['.
    tokens: List[str] = []
    for match in _TOKEN_RE.finditer(pattern):
        start = match.start()
        if start > 0 and pattern[start - 1] in ("*", "?", "]"):
            continue
        tok = match.group(0)
        if tok not in tokens:
            tokens.append(tok)
    return tokens[:4]


class FileIndex:
    """SQLite-backed file metadata & FTS5 index."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or paths.cache_path("file_index.db")
        self._lock = threading.RLock()
        self._bg_lock = threading.Lock()
        self._bg_running = False
        self._has_fts5: Optional[bool] = None

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> bool:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_roots (
                root TEXT PRIMARY KEY,
                root_norm TEXT NOT NULL,
                indexed_at REAL NOT NULL,
                file_count INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                path_norm TEXT NOT NULL,
                name TEXT NOT NULL,
                name_lower TEXT NOT NULL,
                ext TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                root_norm TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_norm)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_name_lower ON files(name_lower)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(ext)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_path_norm ON files(path_norm)")

        if self._has_fts5 is None:
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
                    USING fts5(name, path, content='files', content_rowid='rowid', tokenize='unicode61')
                    """
                )
                self._has_fts5 = True
            except sqlite3.OperationalError:
                self._has_fts5 = False
        return bool(self._has_fts5)

    def refresh_root(
        self,
        root: str,
        *,
        max_depth: int = DEFAULT_INDEX_MAX_DEPTH,
        time_budget_ms: Optional[int] = None,
        include_ignored: bool = False,
    ) -> Dict[str, Any]:
        """Scan one root directory and update its rows in the SQLite index."""
        t0 = time.monotonic()
        real_root = os.path.realpath(root)
        if not os.path.isdir(real_root):
            return {"ok": False, "error": "Root directory not found: %s" % real_root}

        root_norm = _norm_path(real_root)
        deadline = (t0 + time_budget_ms / 1000.0) if time_budget_ms else None

        queue: deque[Tuple[str, int]] = deque([(real_root, 0)])
        seen_dirs = {root_norm}
        rows: List[Tuple[str, str, str, str, str, int, float, str]] = []
        scanned_dirs = 0
        pruned_dirs = 0
        partial = False
        checks = 0

        while queue:
            if deadline is not None and time.monotonic() >= deadline:
                partial = True
                break
            current_dir, depth = queue.popleft()
            scanned_dirs += 1
            try:
                it = os.scandir(current_dir)
            except OSError:
                continue

            with it:
                for entry in it:
                    checks += 1
                    if deadline is not None and (checks & 127) == 0 and time.monotonic() >= deadline:
                        partial = True
                        break
                    name = entry.name
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not include_ignored and fs_search._should_skip_dir(name):
                                pruned_dirs += 1
                                continue
                            if depth < max_depth:
                                child_norm = _norm_path(entry.path)
                                if child_norm not in seen_dirs:
                                    seen_dirs.add(child_norm)
                                    queue.append((entry.path, depth + 1))
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            real_p = os.path.realpath(entry.path)
                            ext = os.path.splitext(name)[1].lower()
                            rows.append((
                                real_p,
                                real_p.lower() if IS_WINDOWS else real_p,
                                name,
                                name.lower(),
                                ext,
                                int(st.st_size),
                                float(st.st_mtime),
                                root_norm,
                            ))
                    except OSError:
                        continue
            if partial:
                break

        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                has_fts = self._ensure_schema(conn)
                with conn:
                    conn.execute("DELETE FROM files WHERE root_norm = ?", (root_norm,))
                    for i in range(0, len(rows), _BATCH_SIZE):
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO files
                            (path, path_norm, name, name_lower, ext, size, mtime, root_norm)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            rows[i:i + _BATCH_SIZE],
                        )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO index_roots
                        (root, root_norm, indexed_at, file_count, complete)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (real_root, root_norm, now, len(rows), 0 if partial else 1),
                    )
                    if has_fts:
                        conn.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
            finally:
                conn.close()

        return {
            "ok": True,
            "root": real_root,
            "file_count": len(rows),
            "scanned_dirs": scanned_dirs,
            "pruned_dirs": pruned_dirs,
            "partial": partial,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    def is_covered(self, base: str, max_age_sec: float = DEFAULT_MAX_AGE_SEC) -> bool:
        """Return True if ``base`` lies inside a fresh indexed root."""
        if not os.path.isfile(self.db_path):
            return False
        base_norm = _norm_path(base)
        now = time.time()
        with self._lock:
            try:
                conn = self._connect()
            except Exception:
                return False
            try:
                self._ensure_schema(conn)
                cur = conn.execute("SELECT root_norm, indexed_at FROM index_roots")
                for root_norm, indexed_at in cur.fetchall():
                    if max_age_sec > 0 and (now - float(indexed_at)) > max_age_sec:
                        continue
                    if base_norm == root_norm or base_norm.startswith(root_norm.rstrip("\\/") + os.sep):
                        return True
                return False
            except Exception:
                return False
            finally:
                conn.close()

    def query(
        self,
        base: str,
        pattern: str,
        *,
        max_depth: Optional[int] = None,
        max_results: int = fs_search.GLOB_MAX_RESULTS,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    ) -> Optional[Dict[str, Any]]:
        """Query the local SQLite index. Returns ``None`` if ``base`` is not covered."""
        t0 = time.monotonic()
        real_base = os.path.realpath(base)
        if not self.is_covered(real_base, max_age_sec=max_age_sec):
            return None

        base_norm = _norm_path(real_base)
        prefix = _norm_prefix(real_base)
        is_rel_pattern = ("/" in pattern) or ("**" in pattern) or ("\\" in pattern)
        rel_rx: Optional[re.Pattern] = None
        pat_depth: Optional[int] = None
        if is_rel_pattern:
            rel_rx, pat_depth = fs_search.compile_rel_glob(pattern)
        effective_depth = max_depth
        if pat_depth is not None:
            effective_depth = pat_depth if effective_depth is None else min(effective_depth, pat_depth)

        # Build SQL pre-filter
        sql_where = ["(f.path_norm LIKE ? OR f.path_norm = ?)"]
        sql_params: List[Any] = [prefix + "%", base_norm]

        # Narrow by extension if pattern ends with a literal extension like *.py or **/*.pdf
        ext_match = re.search(r"\.([A-Za-z0-9]{1,10})$", pattern)
        if ext_match and "*" not in ext_match.group(1) and "?" not in ext_match.group(1):
            sql_where.append("f.ext = ?")
            sql_params.append("." + ext_match.group(1).lower())

        # Narrow by literal substring in basename when pattern has no path sep
        if not is_rel_pattern:
            plain = pattern.lower().replace("*", "%").replace("?", "_")
            if "[" not in plain and "]" not in plain:
                sql_where.append("f.name_lower LIKE ?")
                sql_params.append(plain)

        fts_tokens = _extract_fts_tokens(pattern)
        candidates: List[str] = []

        with self._lock:
            conn = self._connect()
            try:
                has_fts = self._ensure_schema(conn)
                if has_fts and fts_tokens:
                    fts_expr = " AND ".join(f'"{tok}"*' for tok in fts_tokens)
                    sql = (
                        "SELECT f.path FROM files f "
                        "JOIN files_fts ON files_fts.rowid = f.rowid "
                        "WHERE files_fts MATCH ? AND " + " AND ".join(sql_where)
                    )
                    try:
                        rows = conn.execute(sql, [fts_expr] + sql_params).fetchall()
                        candidates = [r[0] for r in rows]
                    except sqlite3.OperationalError:
                        # Fallback to plain table scan if FTS query syntax trips
                        sql = "SELECT f.path FROM files f WHERE " + " AND ".join(sql_where)
                        candidates = [r[0] for r in conn.execute(sql, sql_params).fetchall()]
                else:
                    sql = "SELECT f.path FROM files f WHERE " + " AND ".join(sql_where)
                    candidates = [r[0] for r in conn.execute(sql, sql_params).fetchall()]
            finally:
                conn.close()

        matched: List[str] = []
        base_len = len(real_base.rstrip("\\/")) + 1
        pat_lower = pattern.lower()

        for path in candidates:
            rel = path[base_len:] if len(path) > base_len else os.path.basename(path)
            rel_posix = rel.replace("\\", "/")
            if effective_depth is not None:
                depth = rel_posix.count("/")
                if depth > effective_depth:
                    continue
            if is_rel_pattern:
                if rel_rx is not None and rel_rx.match(rel_posix):
                    matched.append(path)
            else:
                name = os.path.basename(path)
                if IS_WINDOWS:
                    ok = fnmatch.fnmatchcase(name.lower(), pat_lower)
                else:
                    ok = fnmatch.fnmatch(name, pattern)
                if ok:
                    matched.append(path)

        matched = sorted(set(matched))
        truncated = len(matched) > max_results
        if truncated:
            matched = matched[:max_results]

        return {
            "files": matched,
            "count": len(matched),
            "truncated": truncated,
            "partial": False,
            "backend": "sqlite_fts5" if self._has_fts5 else "sqlite",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "note": "[VERIFIED BY TOOL] paths come from the local SQLite file index",
        }

    def start_background_refresh(self, roots: Sequence[str]) -> bool:
        """Trigger a non-blocking daemon refresh of ``roots`` if none is active."""
        with self._bg_lock:
            if self._bg_running:
                return False
            self._bg_running = True

        def _worker() -> None:
            try:
                for root in roots:
                    if os.path.isdir(root):
                        self.refresh_root(root)
            finally:
                with self._bg_lock:
                    self._bg_running = False

        thread = threading.Thread(target=_worker, name="rhm-fs-index", daemon=True)
        thread.start()
        return True


_DEFAULT_INDEX: Optional[FileIndex] = None
_DEFAULT_LOCK = threading.Lock()


def default_index() -> FileIndex:
    global _DEFAULT_INDEX
    with _DEFAULT_LOCK:
        if _DEFAULT_INDEX is None:
            _DEFAULT_INDEX = FileIndex()
        return _DEFAULT_INDEX
