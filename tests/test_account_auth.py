"""Account cabinet: config, OAuth start/callback, session and logout."""

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from account import app as account_app_module
from account import auth, sessions
from account.app import create_app
from account.config import AccountConfig


def make_config(tmp_path):
    return AccountConfig(
        enabled=True,
        client_id="client-id.apps.googleusercontent.com",
        client_secret="client-secret",
        base_url="http://testserver",
        session_secret="s" * 40,
        db_path=str(tmp_path / "account.db"),
    )


def test_unconfigured_cabinet_does_not_break_the_app(tmp_path):
    cfg = AccountConfig(False, "", "", "http://testserver", "", str(tmp_path / "account.db"))
    client = TestClient(create_app(cfg))
    response = client.get("/")
    assert response.status_code == 200
    assert "не настроен" in response.text


def test_login_page_and_start_redirect(tmp_path):
    client = TestClient(create_app(make_config(tmp_path)))
    response = client.get("/login")
    assert response.status_code == 200
    assert "Войти через Google" in response.text

    start = client.get("/auth/google/start?next=/account", follow_redirects=False)
    assert start.status_code == 302
    location = start.headers["location"]
    assert "accounts.google.com" in location
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://testserver/account/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] and query["nonce"]


def test_callback_rejects_unknown_state(tmp_path):
    client = TestClient(create_app(make_config(tmp_path)))
    response = client.get("/auth/google/callback?state=missing&code=x", follow_redirects=False)
    assert response.status_code == 400
    assert "истекла" in response.text


def test_callback_creates_session_and_dashboard(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    app = create_app(cfg)
    client = TestClient(app)

    start = client.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    claims = {
        "iss": auth.ISSUER,
        "sub": "google-sub-1",
        "aud": cfg.client_id,
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
    }
    monkeypatch.setattr(account_app_module.auth, "exchange_code", lambda *a, **k: {"id_token": "fake"})
    monkeypatch.setattr(account_app_module.auth, "verify_id_token", lambda *a, **k: (claims, ""))

    response = client.get("/auth/google/callback?state=%s&code=abc" % state, follow_redirects=False)
    assert response.status_code == 303
    # The dashboard opens with the remote-control modal after a login.
    assert response.headers["location"].startswith("/account")
    assert client.cookies.get("rhm_session")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "user@example.com" in dashboard.text

    # Logout needs the per-session CSRF token.
    cookie = client.cookies.get("rhm_session")
    sid = sessions.decode(cookie, cfg.session_secret)
    session = app.state.account_store.session(sid)
    assert session is not None
    bad = client.post("/logout", data={"csrf": "wrong"}, follow_redirects=False)
    assert bad.status_code == 403
    good = client.post("/logout", data={"csrf": session["csrf"]}, follow_redirects=False)
    assert good.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_sanitize_next_blocks_external_targets():
    assert auth.sanitize_next("//evil") == "/account"
    assert auth.sanitize_next("https://evil") == "/account"
    assert auth.sanitize_next("/account/devices") == "/account/devices"
