"""SQLite storage for the account cabinet.

Deliberately tiny and dependency-free: one connection per operation, WAL, and
idempotent DDL.  The rest of the cabinet (devices/agents/usage) will add tables
to the same database; nothing here touches the node's registry.json.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import secrets
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    google_iss TEXT NOT NULL,
    google_sub TEXT NOT NULL,
    aud TEXT NOT NULL,
    email TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    name TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    share_usage INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE (google_iss, google_sub)
);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    owner_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'personal',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_sessions (
    id_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT,
    csrf TEXT NOT NULL,
    ip_hash TEXT,
    ua_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_account_sessions_account ON account_sessions(account_id);

CREATE TABLE IF NOT EXISTS auth_flows (
    state TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    verifier TEXT NOT NULL,
    next_path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'login',
    account_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    node_name TEXT UNIQUE NOT NULL,
    display_name TEXT,
    claimed_at TEXT,
    last_seen_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS google_tokens (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    scopes TEXT,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _future(seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Databases created before Drive support need the two flow columns.
            for column, ddl in (
                ("kind", "ALTER TABLE auth_flows ADD COLUMN kind TEXT NOT NULL DEFAULT 'login'"),
                ("account_id", "ALTER TABLE auth_flows ADD COLUMN account_id TEXT"),
            ):
                existing = {row["name"] for row in conn.execute("PRAGMA table_info(auth_flows)")}
                if column not in existing:
                    conn.execute(ddl)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    # -- accounts ---------------------------------------------------------
    def upsert_account(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        iss = str(claims.get("iss") or "")
        sub = str(claims.get("sub") or "")
        email = str(claims.get("email") or "")
        name = str(claims.get("name") or "")
        aud = str(claims.get("aud") or "")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO accounts
                   (id, google_iss, google_sub, aud, email, email_verified, name, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(google_iss, google_sub) DO UPDATE SET
                     email=excluded.email, name=excluded.name, aud=excluded.aud,
                     email_verified=excluded.email_verified, last_login_at=excluded.last_login_at""",
                (
                    secrets.token_hex(16), iss, sub, aud, email,
                    1 if claims.get("email_verified") else 0, name, now_iso(), now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM accounts WHERE google_iss=? AND google_sub=?", (iss, sub)
            ).fetchone()
            account = dict(row)
            tenant = conn.execute(
                "SELECT * FROM tenants WHERE owner_account_id=? LIMIT 1", (account["id"],)
            ).fetchone()
            if tenant is None:
                tenant_id = secrets.token_hex(16)
                conn.execute(
                    "INSERT INTO tenants (id, owner_account_id, name, created_at) VALUES (?, ?, ?, ?)",
                    (tenant_id, account["id"], email or "Personal", now_iso()),
                )
                tenant = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            account["tenant_id"] = tenant["id"]
            return account

    # -- sessions ---------------------------------------------------------
    def create_session(self, account_id: str, ttl_seconds: int = 7 * 86400) -> Dict[str, Any]:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO account_sessions
                   (id_hash, account_id, created_at, idle_expires_at, absolute_expires_at, last_seen_at, csrf)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    hashlib.sha256(sid.encode()).hexdigest(), account_id, now_iso(),
                    _future(ttl_seconds), _future(30 * 86400), now_iso(), csrf,
                ),
            )
        return {"id": sid, "csrf": csrf}

    def session(self, sid: str) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        key = hashlib.sha256(sid.encode()).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT s.*, a.id AS id, a.email, a.name,
                          (SELECT id FROM tenants WHERE owner_account_id=a.id LIMIT 1) AS tenant_id
                   FROM account_sessions s
                   JOIN accounts a ON a.id = s.account_id
                   WHERE s.id_hash=? AND s.revoked_at IS NULL
                     AND s.idle_expires_at > ? AND s.absolute_expires_at > ?""",
                (key, now_iso(), now_iso()),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE account_sessions SET last_seen_at=?, idle_expires_at=? WHERE id_hash=?",
                (now_iso(), _future(7 * 86400), key),
            )
            return dict(row)

    def revoke_session(self, sid: str) -> None:
        key = hashlib.sha256(sid.encode()).hexdigest()
        with self._connect() as conn:
            conn.execute(
                "UPDATE account_sessions SET revoked_at=? WHERE id_hash=?", (now_iso(), key)
            )

    # -- OAuth flows ------------------------------------------------------
    def create_flow(self, state: str, nonce: str, verifier: str, next_path: str,
                    kind: str = "login", account_id: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO auth_flows
                   (state, nonce, verifier, next_path, kind, account_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (state, nonce, verifier, next_path, kind, account_id or None,
                 now_iso(), _future(600)),
            )

    def consume_flow(self, state: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_flows WHERE state=? AND used_at IS NULL AND expires_at > ?",
                (state, now_iso()),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE auth_flows SET used_at=? WHERE state=?", (now_iso(), state))
            return dict(row)

    # -- devices ----------------------------------------------------------
    def device_count(self, tenant_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM devices WHERE tenant_id=? AND status='active'",
                (tenant_id,),
            ).fetchone()
            return int(row["n"] if row else 0)

    # -- Google Drive tokens (encrypted payload; per account) -------------
    def save_google_tokens(self, account_id: str, payload: str, scopes: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO google_tokens (account_id, payload, scopes, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     payload=excluded.payload, scopes=excluded.scopes,
                     updated_at=excluded.updated_at""",
                (account_id, payload, scopes, now_iso()),
            )

    def google_tokens(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM google_tokens WHERE account_id=?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_google_tokens(self, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM google_tokens WHERE account_id=?", (account_id,))

    def list_devices(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM devices
                   WHERE tenant_id=? AND status='active'
                   ORDER BY COALESCE(claimed_at, '') DESC, node_name""",
                (tenant_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def device(self, device_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """One device of this tenant; a foreign id answers ``None`` (never a 403)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE id=? AND tenant_id=? AND status='active'",
                (str(device_id or ""), str(tenant_id or "")),
            ).fetchone()
            return dict(row) if row else None

    def claim_device(self, tenant_id: str, node_name: str,
                     display_name: str = "") -> Optional[Dict[str, Any]]:
        """Link *node_name* to this tenant once ownership has been proven.

        A node already linked to a different tenant is never re-linked: the
        caller gets ``None`` (and the page a generic "already linked" message, so
        the cabinet cannot be used to enumerate somebody else's nodes).
        """
        node = str(node_name or "").strip().lower()
        if not node:
            return None
        label = str(display_name or "").strip()[:120] or node
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM devices WHERE node_name=?", (node,)
            ).fetchone()
            if existing is not None:
                if existing["tenant_id"] != tenant_id:
                    return None
                conn.execute(
                    """UPDATE devices SET display_name=?, claimed_at=?, status='active'
                       WHERE id=?""",
                    (label, now_iso(), existing["id"]),
                )
                row = conn.execute("SELECT * FROM devices WHERE id=?", (existing["id"],)).fetchone()
                return dict(row)
            device_id = "dev_" + secrets.token_hex(12)
            conn.execute(
                """INSERT INTO devices (id, tenant_id, node_name, display_name, claimed_at, status)
                   VALUES (?, ?, ?, ?, ?, 'active')""",
                (device_id, tenant_id, node, label, now_iso()),
            )
            row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            return dict(row)

    def touch_device(self, device_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at=? WHERE id=?", (now_iso(), str(device_id))
            )
