"""At-rest encryption for per-user Google tokens.

The key is ``MESH_TOKEN_KEY`` when set (recommended), otherwise derived from
``MESH_SESSION_SECRET`` so an existing single-secret deployment keeps working.
Tokens never go to logs or to the browser; the cabinet only sees metadata.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any, Dict


def _fernet(secret: str):
    from cryptography.fernet import Fernet

    material = os.environ.get("MESH_TOKEN_KEY", "").strip() or secret
    key = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(payload: Dict[str, Any], secret: str) -> str:
    return _fernet(secret).encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()


def decrypt(blob: str, secret: str) -> Dict[str, Any]:
    raw = _fernet(secret).decrypt(str(blob).encode())
    value = json.loads(raw.decode())
    return value if isinstance(value, dict) else {}
