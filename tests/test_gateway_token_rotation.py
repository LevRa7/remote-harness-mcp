"""Device-token rotation: the pending token, and the moment the old one dies.

The device token is the node's identity, so rotating it is the one operation that
can lock a machine out of its own gateway. The design under test:

* the rotation endpoint hands out a new token while the current one keeps
  working;
* the current one is retired when the node opens a tunnel with the new one - the
  only proof that the new secret actually reached the config;
* an operator can retire it immediately (``rotate-token/finish``) when the token
  is known to be in someone else's hands.
"""

import asyncio
import json
from urllib.parse import urlparse

import pytest
from starlette.requests import Request

import gateway


def _request(query: str = "", method: str = "GET", body: bytes = b"",
             headers: dict | None = None, path_params: dict | None = None,
             path: str = "/v1/devices") -> Request:
    raw_headers = [(b"host", b"smart-server.online"), (b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "query_string": urlparse(f"/{query}").query.encode(),
        "scheme": "https",
        "server": ("smart-server.online", 443),
        "client": ("127.0.0.1", 12345),
        "path_params": path_params or {},
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "REGISTRY_PATH", str(tmp_path / "registry.json"))
    monkeypatch.setattr(gateway, "ADMIN_TOKEN", "admin-secret")
    gateway._rate_windows.clear()
    yield
    gateway._rate_windows.clear()


def _register(username: str = "node-one") -> dict:
    body = json.dumps({"username": username, "os": "linux"}).encode()
    response = asyncio.run(gateway.api_register(_request(method="POST", body=body)))
    return json.loads(response.body)


def _registry() -> dict:
    try:
        with open(gateway.REGISTRY_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}


def _rotate(username: str, token: str, headers: dict | None = None):
    merged = {"Authorization": "Bearer %s" % token} if token else {}
    merged.update(headers or {})
    return asyncio.run(gateway.device_rotate_token(_request(
        method="POST", path_params={"user": username}, headers=merged,
    )))


def _finish(username: str, headers: dict):
    return asyncio.run(gateway.device_rotate_token_finish(_request(
        method="POST", path_params={"user": username}, headers=headers,
    )))


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

def test_rotation_needs_the_current_token_or_admin():
    data = _register()
    assert _rotate("node-one", "").status_code == 403
    assert _rotate("node-one", "not-the-token").status_code == 403
    # The link key runs tools; it must not be able to rotate the identity.
    assert _rotate("node-one", data["link_key"]).status_code == 403
    assert _rotate("node-one", data["token"]).status_code == 200
    assert _rotate("node-one", "", headers={"X-Admin-Token": "admin-secret"}).status_code == 200


def test_rotation_of_an_unknown_node_is_404():
    assert _rotate("ghost", "whatever").status_code == 404
    assert _finish("ghost", {"X-Admin-Token": "admin-secret"}).status_code == 404


# ---------------------------------------------------------------------------
# Two-phase rotation
# ---------------------------------------------------------------------------

def test_a_rotated_token_works_before_the_old_one_dies():
    data = _register()
    response = _rotate("node-one", data["token"])
    issued = json.loads(response.body)["token"]

    assert issued != data["token"]
    assert _registry()["node-one"]["token_next_sha256"] == gateway.hash_secret(issued)

    # Both work while the rotation is pending: the node has not restarted yet.
    old_ok, _m, _u = gateway.verify_token(_request(
        "?user=node-one", headers={"Authorization": "Bearer %s" % data["token"]}))
    new_ok, _m, _u = gateway.verify_token(_request(
        "?user=node-one", headers={"Authorization": "Bearer %s" % issued}))
    assert old_ok is True
    assert new_ok is True


def test_the_old_token_dies_when_the_node_proves_the_new_one():
    data = _register()
    issued = json.loads(_rotate("node-one", data["token"]).body)["token"]

    registry = gateway.load_registry()
    promoted = gateway.promote_device_token("node-one", registry, registry["node-one"])
    assert promoted is True

    entry = _registry()["node-one"]
    assert "token_next_sha256" not in entry
    assert entry["token_sha256"] == gateway.hash_secret(issued)

    old_ok, _m, _u = gateway.verify_token(_request(
        "?user=node-one", headers={"Authorization": "Bearer %s" % data["token"]}))
    new_ok, _m, _u = gateway.verify_token(_request(
        "?user=node-one", headers={"Authorization": "Bearer %s" % issued}))
    assert old_ok is False
    assert new_ok is True


def test_promoting_without_a_pending_rotation_changes_nothing():
    _register()
    registry = gateway.load_registry()
    assert gateway.promote_device_token("node-one", registry, registry["node-one"]) is False


def test_finish_retires_the_old_token_immediately():
    data = _register()
    issued = json.loads(_rotate("node-one", data["token"]).body)["token"]

    # The new token may close the rotation itself, without waiting for a reconnect.
    response = _finish("node-one", {"Authorization": "Bearer %s" % issued})
    assert response.status_code == 200
    old_ok, _m, _u = gateway.verify_token(_request(
        "?user=node-one", headers={"Authorization": "Bearer %s" % data["token"]}))
    assert old_ok is False


def test_finish_refuses_when_no_rotation_is_pending():
    data = _register()
    assert _finish("node-one", {"Authorization": "Bearer %s" % data["token"]}).status_code == 409


def test_a_rotation_also_drops_legacy_plaintext_credentials():
    """A pre-0.4.6 node keeps a plaintext token and a legacy-query allowance.

    Rotating has to remove both, otherwise the leaked secret the operator is
    trying to kill would survive in the registry.
    """
    gateway.save_registry({"old-node": {
        "token": "legacy-token",
        "allow_legacy_query": True,
        "link_key": "lk_" + "0" * 32,
        "created_at": "2025-01-01",
    }})
    registry = gateway.load_registry()
    entry = registry["old-node"]
    entry["token_next_sha256"] = gateway.hash_secret("brand-new-token")

    gateway.promote_device_token("old-node", registry, entry)

    entry = _registry()["old-node"]
    assert "token" not in entry
    assert "allow_legacy_query" not in entry
    assert entry["token_sha256"] == gateway.hash_secret("brand-new-token")
