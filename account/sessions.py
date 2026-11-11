"""Signed session cookie helpers (no itsdangerous dependency)."""

from __future__ import annotations

import base64
import hashlib
import hmac

from starlette.requests import Request
from starlette.responses import Response


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign(value: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    return _b64(digest)


def encode(value: str, secret: str) -> str:
    return _b64(value.encode()) + "." + sign(value, secret)


def decode(cookie: str, secret: str) -> str:
    try:
        body, signature = cookie.rsplit(".", 1)
        value = _unb64(body).decode()
    except Exception:
        return ""
    if not hmac.compare_digest(signature, sign(value, secret)):
        return ""
    return value


def cookie_name(base_url: str) -> str:
    return "__Host-rhm_session" if base_url.startswith("https://") else "rhm_session"


def session_id(request: Request, secret: str, base_url: str) -> str:
    raw = request.cookies.get(cookie_name(base_url), "")
    return decode(raw, secret) if raw else ""


def set_cookie(response: Response, sid: str, secret: str, base_url: str, max_age: int = 7 * 86400) -> None:
    response.set_cookie(
        cookie_name(base_url),
        encode(sid, secret),
        max_age=max_age,
        httponly=True,
        secure=base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


def clear_cookie(response: Response, base_url: str) -> None:
    response.delete_cookie(cookie_name(base_url), path="/")
