"""Google OIDC: start, callback and ID-token verification."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"
ISSUER = "https://accounts.google.com"
SCOPES = (
    "openid "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)
DRIVE_SCOPES = SCOPES + " https://www.googleapis.com/auth/drive.file"

_JWKS: Dict[str, Any] = {"fetched": 0.0, "keys": []}


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def new_verifier() -> str:
    return secrets.token_urlsafe(48)


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sanitize_next(value: Any) -> str:
    """Only a local absolute path; never a scheme-relative or external URL."""
    raw = str(value or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/account"
    return raw[:500]


def build_auth_url(config, state: str, nonce: str, verifier: str,
                   scopes: str = SCOPES, access_type: str = "online",
                   prompt: str = "select_account") -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge(verifier),
        "code_challenge_method": "S256",
        "access_type": access_type,
        "prompt": prompt,
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _post_form(url: str, data: Dict[str, str], timeout: int = 15) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def exchange_code(config, code: str, verifier: str) -> Dict[str, Any]:
    return _post_form(TOKEN_ENDPOINT, {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })


def refresh_access_token(config, refresh_token: str) -> Dict[str, Any]:
    """Offline refresh; the response usually has no new refresh_token."""
    return _post_form(TOKEN_ENDPOINT, {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })


def _jwks() -> Dict[str, Any]:
    if _JWKS["keys"] and time.time() - _JWKS["fetched"] < 3600:
        return {"keys": _JWKS["keys"]}
    with urllib.request.urlopen(JWKS_ENDPOINT, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    _JWKS["keys"] = payload.get("keys", [])
    _JWKS["fetched"] = time.time()
    return {"keys": _JWKS["keys"]}


def _key_for(kid: str) -> Optional[Dict[str, Any]]:
    for key in _jwks().get("keys", []):
        if key.get("kid") == kid:
            return key
    # Unknown kid may mean a key rotation: refresh once.
    _JWKS["keys"] = []
    for key in _jwks().get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


def verify_id_token(config, token: str, nonce: str) -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        import jwt
        from jwt import algorithms
    except Exception:
        return None, "OIDC dependencies are missing on the gateway (PyJWT/cryptography)."
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        return None, "Malformed ID token: %s" % exc
    kid = header.get("kid", "")
    key_data = _key_for(kid)
    if key_data is None:
        return None, "Unknown signing key (kid)."
    try:
        key = algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        claims = jwt.decode(
            token, key=key, algorithms=["RS256"], audience=config.client_id,
            issuer=ISSUER, options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            leeway=60,
        )
    except Exception as exc:
        return None, "ID token rejected: %s" % exc
    if claims.get("nonce") != nonce:
        return None, "ID token nonce mismatch."
    if not claims.get("email_verified"):
        return None, "Google account email is not verified."
    return claims, ""
