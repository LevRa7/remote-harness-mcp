"""Drive: per-account OAuth, encrypted token storage, cabinet list."""

from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from account import app as account_app_module
from account import auth, crypto, drive as drive_api, sessions
from account.app import create_app
from account.config import AccountConfig


def config(tmp_path):
    return AccountConfig(True, "client-id", "client-secret", "http://testserver",
                         "s" * 40, str(tmp_path / "account.db"))


def signed_in(app):
    store = app.state.account_store
    account = store.upsert_account({
        "iss": auth.ISSUER, "sub": "drive-sub", "aud": "client-id",
        "email": "drive@example.com", "email_verified": True, "name": "Drive",
    })
    session = store.create_session(account["id"])
    client = TestClient(app)
    client.cookies.set("rhm_session", sessions.encode(session["id"], "s" * 40))
    return client, account


def test_drive_start_requests_narrow_scope(tmp_path):
    app = create_app(config(tmp_path))
    client, _ = signed_in(app)
    response = client.get("/drive/connect", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert "drive.file" in query["scope"][0]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]


def test_drive_callback_stores_encrypted_tokens(tmp_path, monkeypatch):
    app = create_app(config(tmp_path))
    client, account = signed_in(app)
    start = client.get("/drive/connect", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    monkeypatch.setattr(account_app_module.auth, "exchange_code", lambda *a, **k: {
        "access_token": "ya29.secret", "refresh_token": "1//refresh",
        "expires_in": 3600, "id_token": "fake",
    })
    monkeypatch.setattr(account_app_module.auth, "verify_id_token", lambda *a, **k: (
        {"iss": auth.ISSUER, "sub": "drive-sub", "aud": "client-id",
         "email": "drive@example.com", "email_verified": True}, ""))
    response = client.get("/auth/google/callback?state=%s&code=ok" % state,
                          follow_redirects=False)
    assert response.status_code == 303
    record = app.state.account_store.google_tokens(account["id"])
    assert record is not None
    assert "ya29.secret" not in record["payload"]
    tokens = crypto.decrypt(record["payload"], "s" * 40)
    assert tokens["refresh_token"] == "1//refresh"


def test_drive_page_lists_visible_files(tmp_path, monkeypatch):
    app = create_app(config(tmp_path))
    client, account = signed_in(app)
    from account import crypto as crypto_module
    app.state.account_store.save_google_tokens(
        account["id"],
        crypto_module.encrypt({"access_token": "t", "refresh_token": "r",
                               "expires_at": "2999-01-01T00:00:00+00:00"}, "s" * 40),
        auth.DRIVE_SCOPES,
    )
    monkeypatch.setattr(account_app_module.drive_api, "access_token", lambda *a, **k: "t")
    monkeypatch.setattr(account_app_module.drive_api, "list_files", lambda *a, **k: [
        {"name": "report.pdf", "mimeType": "application/pdf", "modifiedTime": "2026-01-01"}
    ])
    page = client.get("/drive")
    assert page.status_code == 200
    assert "report.pdf" in page.text
