"""Configuration for the account cabinet (Wave 1/2).

Everything is optional: without ``MESH_ACCOUNT=1`` and Google credentials the
cabinet renders a "not configured" page while the MCP transport keeps working.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from core import domain, paths

TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AccountConfig:
    enabled: bool
    client_id: str
    client_secret: str
    base_url: str
    session_secret: str
    db_path: str

    @property
    def redirect_uri(self) -> str:
        return self.base_url.rstrip("/") + "/account/auth/google/callback"

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.client_id and self.client_secret and self.session_secret)

    @classmethod
    def from_env(cls) -> "AccountConfig":
        enabled = os.environ.get("MESH_ACCOUNT", "0").strip().lower() in TRUE
        base = (
            os.environ.get("MESH_ACCOUNT_BASE_URL", "").strip()
            or os.environ.get("MESH_PUBLIC_URL", "").strip()
            or domain.public_base_url()
        ).rstrip("/")
        try:
            parsed = urlparse(base)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                base = domain.public_base_url().rstrip("/")
        except Exception:
            base = domain.public_base_url().rstrip("/")
        db_path = os.environ.get("MESH_ACCOUNT_DB", "").strip() or os.path.join(
            paths.config_dir(), "account.db"
        )
        return cls(
            enabled=enabled,
            client_id=os.environ.get("MESH_GOOGLE_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("MESH_GOOGLE_CLIENT_SECRET", "").strip(),
            base_url=base,
            session_secret=os.environ.get("MESH_SESSION_SECRET", "").strip(),
            db_path=db_path,
        )
