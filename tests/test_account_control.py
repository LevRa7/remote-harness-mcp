"""Cabinet: device claim, the remote-control switch and the symmetric control API.

The cabinet never writes a node's settings itself: it forwards the same
``node_control`` tool a model calls (through the gateway bridge, which stamps
``_origin=web``). These tests use a fake bridge to pin the pages, the CSRF rules
and the exact payload - the node-side behaviour is covered by
``tests/test_node_control.py``.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from account import app as account_app_module
from account import auth, sessions
from account.app import create_app
from account.config import AccountConfig


class FakeBridge:
    """Stands in for gateway.CabinetNodeControl."""

    def __init__(self):
        self.state = {
            "access_mode": "full",
            "read_only": False,
            "paused": False,
            "workspace": "/home/user/project",
            "online": True,
                    "remote_control": {"enabled": True},
            "sandbox": {"enabled": False, "workspace": None, "container": None},
            "modes": ["read_only", "full"],
        }
        self.calls = []
        self.claim_calls = []
        self.claim_answer = {"ok": True, "node": "desk"}
        #: What the bridge's registry snapshot reports; a test fills it in.
        self.registry = {}
        #: Pending pairing codes the bridge knows, and the approvals it saw.
        self.pairs = {}
        self.approve_calls = []
        self.approve_answer = {"ok": True, "username": "desk-pc", "hostname": "desk-pc"}

    def available(self):
        return True

    def online(self, node):
        return True

    def devices(self):
        return {name: dict(entry) for name, entry in self.registry.items()}

    def claim(self, node, credential, owner_subject="", account_id=""):
        self.claim_calls.append({
            "node": node,
            "credential": credential,
            "owner_subject": owner_subject,
            "account_id": account_id,
        })
        return dict(self.claim_answer)

    def pair(self, code):
        entry = self.pairs.get(code)
        return dict(entry) if entry else None

    def approve_pair(self, code, owner_subject="", account_id=""):
        self.approve_calls.append({
            "code": code,
            "owner_subject": owner_subject,
            "account_id": account_id,
        })
        answer = dict(self.approve_answer)
        if answer.get("ok") and code in self.pairs:
            self.pairs[code]["approved"] = True
        return answer

    async def call(self, node, payload):
        self.calls.append((node, dict(payload)))
        action = payload.get("action")
        if action == "status":
            return dict(self.state)
        if action == "remote_control":
            self.state["remote_control"]["enabled"] = bool(payload.get("enabled"))
            return {"ok": True, "message": "remote control is OFF", "state": self.state}
        return {"ok": True, "message": "ok", "state": self.state}


def make_config(tmp_path):
    return AccountConfig(
        enabled=True,
        client_id="client-id.apps.googleusercontent.com",
        client_secret="client-secret",
        base_url="http://testserver",
        session_secret="s" * 40,
        db_path=str(tmp_path / "account.db"),
    )


def login(client, app, cfg, monkeypatch):
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
    response = client.get("/auth/google/callback?state=%s&code=abc" % state,
                          follow_redirects=False)
    assert response.status_code == 303
    sid = sessions.decode(client.cookies.get("rhm_session"), cfg.session_secret)
    session = app.state.account_store.session(sid)
    assert session is not None
    return session


def claim_device(client, session, node="desk", credential="lk_secret"):
    return client.post("/devices/claim",
                       data={"csrf": session["csrf"], "node": node,
                             "credential": credential},
                       follow_redirects=False)


def device_id(app, session):
    devices = app.state.account_store.list_devices(session["tenant_id"])
    assert devices
    return devices[0]["id"]


@pytest.fixture()
def cabinet(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    bridge = FakeBridge()
    app = create_app(cfg, node_control=bridge)
    client = TestClient(app)
    session = login(client, app, cfg, monkeypatch)
    return client, app, bridge, session


# ---------------------------------------------------------------------------
# The setting right after login
# ---------------------------------------------------------------------------

def test_dashboard_asks_to_claim_a_device_first(cabinet):
    client = cabinet[0]
    page = client.get("/")
    assert page.status_code == 200
    assert "Привяжите устройство" in page.text
    assert "lk_" in page.text


def test_the_first_visit_after_login_shows_the_lock_as_a_modal(cabinet):
    client = cabinet[0]
    page = client.get("/?welcome=1")
    assert "class=modal" in page.text
    assert "Понятно" in page.text
    assert "Удалённое управление доступом" in page.text or "Привяжите устройство" in page.text

    # Without the flag it is an ordinary page, not a modal.
    assert "class=modal" not in client.get("/").text


def test_claim_links_the_device_and_shows_the_switch(cabinet):
    client, app, bridge, session = cabinet
    response = claim_device(client, session)
    assert response.status_code == 303
    assert "/devices/" in response.headers["location"]

    page = client.get("/")
    assert "desk" in page.text
    assert "Удалённое управление MCP" in page.text
    assert "включено" in page.text
    assert bridge.calls and bridge.calls[0][1]["action"] == "status"


def test_a_rejected_credential_links_nothing(cabinet):
    client, app, bridge, session = cabinet
    bridge.claim_answer = {"ok": False, "error": "bad_credential"}

    response = claim_device(client, session, credential="lk_wrong")
    assert response.status_code == 303
    assert "bad_credential" not in response.headers["location"]
    assert app.state.account_store.list_devices(session["tenant_id"]) == []


def test_claim_without_csrf_is_refused(cabinet):
    client, app, _, session = cabinet
    response = client.post("/devices/claim", data={"node": "desk", "credential": "lk_secret"})
    assert response.status_code == 403
    assert app.state.account_store.list_devices(session["tenant_id"]) == []


# ---------------------------------------------------------------------------
# Device settings page
# ---------------------------------------------------------------------------

def test_device_page_renders_every_switch(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    page = client.get("/devices/%s" % device_id(app, session))
    assert page.status_code == 200
    for text in ("Удалённое управление доступом", "Режим доступа", "Рабочая папка",
                 "Изолированная среда", "Поставить на паузу"):
        assert text in page.text


def test_a_disabled_lock_shows_how_to_restore_it_and_hides_the_controls(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    bridge.state["remote_control"]["enabled"] = False

    page = client.get("/devices/%s" % device_id(app, session))
    assert "rhm remote-control on" in page.text
    assert "Удалённое управление выключено" in page.text
    # No mode form: offering a button that the node would refuse is a lie.
    assert "Режим доступа" not in page.text


def test_a_foreign_device_is_a_404(cabinet):
    client = cabinet[0]
    assert client.get("/devices/dev_does_not_exist").status_code == 404


# ---------------------------------------------------------------------------
# Control API
# ---------------------------------------------------------------------------

def test_control_posts_exactly_the_tool_payload(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    device = device_id(app, session)

    response = client.post("/devices/%s/control" % device,
                           data={"csrf": session["csrf"], "action": "mode",
                                 "mode": "read_only"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert ("desk", {"action": "mode", "mode": "read_only"}) in bridge.calls


def test_the_lock_can_be_switched_off_from_the_cabinet(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    device = device_id(app, session)

    client.post("/devices/%s/control" % device,
                data={"csrf": session["csrf"], "action": "remote_control",
                      "enabled": "false"},
                follow_redirects=False)
    assert ("desk", {"action": "remote_control", "enabled": False}) in bridge.calls
    assert bridge.state["remote_control"]["enabled"] is False


def test_isolation_payload_carries_the_network_choice(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    device = device_id(app, session)

    client.post("/devices/%s/control" % device,
                data={"csrf": session["csrf"], "action": "sandbox", "enabled": "true",
                      "workspace": "/home/user/project"},
                follow_redirects=False)
    payload = bridge.calls[-1][1]
    assert payload["action"] == "sandbox"
    assert payload["enabled"] is True
    assert payload["network"] is False

    client.post("/devices/%s/control" % device,
                data={"csrf": session["csrf"], "action": "sandbox", "enabled": "true",
                      "workspace": "/home/user/project", "network": "true"},
                follow_redirects=False)
    assert bridge.calls[-1][1]["network"] is True


def test_control_without_csrf_is_refused(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    device = device_id(app, session)
    before = len(bridge.calls)

    response = client.post("/devices/%s/control" % device,
                           data={"action": "pause"}, follow_redirects=False)
    assert response.status_code == 403
    assert len(bridge.calls) == before


def test_a_node_error_is_shown_on_the_redirect(cabinet, monkeypatch):
    client, app, bridge, session = cabinet
    claim_device(client, session)
    device = device_id(app, session)

    async def failing(node, payload):
        bridge.calls.append((node, dict(payload)))
        return {"error": "remote_control_disabled: off"}

    monkeypatch.setattr(bridge, "call", failing)
    response = client.post("/devices/%s/control" % device,
                           data={"csrf": session["csrf"], "action": "pause"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_a_device_cannot_be_claimed_by_a_second_tenant(cabinet, tmp_path):
    client, app, _, session = cabinet
    store = app.state.account_store
    first = store.claim_device(session["tenant_id"], "desk")
    assert first is not None
    second = store.claim_device("tenant-other", "desk")
    assert second is None
    assert store.device(first["id"], "tenant-other") is None


# ---------------------------------------------------------------------------
# P1-A: ownership recorded in the registry and the merged device list
# ---------------------------------------------------------------------------

OWN_SUBJECT = "google:google-sub-1"


def registry_device(**overrides):
    entry = {
        "hostname": "desk-pc",
        "os": "Linux",
        "arch": "x86_64",
        "owner_subject": OWN_SUBJECT,
        "account_id": "",
        "last_seen": "2026-11-19T07:00:00+00:00",
        "online": True,
    }
    entry.update(overrides)
    return entry


def test_claim_tells_the_bridge_who_owns_the_node(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)

    assert bridge.claim_calls
    call = bridge.claim_calls[-1]
    assert call["node"] == "desk"
    assert call["credential"] == "lk_secret"
    assert call["owner_subject"] == OWN_SUBJECT
    assert call["account_id"] == session["id"]


def test_a_registry_device_with_the_same_owner_is_listed_without_a_manual_claim(cabinet):
    client, app, bridge, session = cabinet
    bridge.registry["desk"] = registry_device(
        link_key="lk_must_not_render", token_sha256="deadbeef")

    page = client.get("/")
    assert page.status_code == 200
    # A node the owner already proved (installed/pair-approved) shows up on its
    # own: no link key needs to be pasted again.
    assert "desk" in page.text
    assert "desk-pc" in page.text
    assert "Linux" in page.text
    assert "adopted" in page.text
    assert "lk_must_not_render" not in page.text
    assert "deadbeef" not in page.text

    devices = app.state.account_store.list_devices(session["tenant_id"])
    assert [item["node_name"] for item in devices] == ["desk"]
    assert devices[0]["display_name"] == "desk-pc"
    assert devices[0]["claimed_at"]


def test_a_registry_device_is_adopted_once_not_on_every_visit(cabinet):
    client, app, bridge, session = cabinet
    bridge.registry["desk"] = registry_device()

    client.get("/")
    client.get("/")
    devices = app.state.account_store.list_devices(session["tenant_id"])
    assert [item["node_name"] for item in devices] == ["desk"]


def test_another_owners_registry_device_is_not_listed(cabinet):
    client, app, bridge, session = cabinet
    bridge.registry["stranger"] = registry_device(
        hostname="stranger-pc", owner_subject="google:someone-else",
        account_id="account-other")

    page = client.get("/")
    assert "stranger" not in page.text
    assert app.state.account_store.list_devices(session["tenant_id"]) == []


def test_a_database_row_without_a_registry_entry_is_marked_missing(cabinet):
    client, app, bridge, session = cabinet
    claim_device(client, session)                 # registry snapshot stays empty
    assert bridge.registry == {}

    page = client.get("/")
    assert page.status_code == 200
    assert "desk" in page.text
    assert "нет связи с реестром" in page.text


# ---------------------------------------------------------------------------
# P1-C: the opt-in list of unclaimed devices
# ---------------------------------------------------------------------------

def unclaimed_part(text):
    """Everything after the section heading; it only exists when the flag is on."""
    return text.split("Непривязанные устройства", 1)[1]


def unclaimed_table(text):
    """The metadata table alone - the claim form below it is not part of it."""
    return unclaimed_part(text).split("<form", 1)[0]


def test_the_unclaimed_section_is_off_by_default(cabinet, monkeypatch):
    monkeypatch.delenv("MESH_CABINET_SHOW_UNCLAIMED", raising=False)
    client, app, bridge, _ = cabinet
    bridge.registry["spare"] = registry_device(
        hostname="spare-pc", owner_subject="", account_id="")

    page = client.get("/")
    assert page.status_code == 200
    # A shared gateway must not advertise machines nobody has claimed yet.
    assert "Непривязанн" not in page.text
    assert "spare" not in page.text


def test_the_unclaimed_section_lists_the_node_and_offers_the_claim_form(cabinet, monkeypatch):
    monkeypatch.setenv("MESH_CABINET_SHOW_UNCLAIMED", "1")
    client, app, bridge, session = cabinet
    bridge.registry["spare"] = registry_device(
        hostname="spare-pc", os="Linux", arch="x86_64", owner_subject="", account_id="")

    page = client.get("/")
    assert page.status_code == 200
    section = unclaimed_part(page.text)
    assert "spare" in section
    assert "spare-pc" in section
    assert "Linux x86_64" in section
    assert "2026-11-19T07:00:00+00:00" in section
    # The linking form: CSRF, the fixed node name and the proof itself.
    assert "action=\"/account/devices/claim\"" in section
    assert "name=csrf" in section
    assert session["csrf"] in section
    assert "name=node" in section
    assert "value=\"spare\" readonly" in section
    assert "name=credential" in section
    assert "type=password" in section
    assert "lk_... или токен устройства" in section


def test_a_foreign_owners_node_never_appears_in_the_unclaimed_section(cabinet, monkeypatch):
    monkeypatch.setenv("MESH_CABINET_SHOW_UNCLAIMED", "1")
    client, app, bridge, _ = cabinet
    bridge.registry["stranger"] = registry_device(
        hostname="stranger-pc", owner_subject="google:someone-else",
        account_id="account-other")

    page = client.get("/")
    assert "stranger" not in page.text
    # Nothing is unclaimed, so the section says exactly that.
    assert "Непривязанных устройств нет" in page.text


def test_the_unclaimed_section_renders_no_keys(cabinet, monkeypatch):
    monkeypatch.setenv("MESH_CABINET_SHOW_UNCLAIMED", "1")
    client, app, bridge, _ = cabinet
    bridge.registry["spare"] = registry_device(
        hostname="spare-pc", owner_subject="", account_id="",
        link_key="lk_supersecret_value", token="tok_supersecret_value",
        token_sha256="sha_supersecret_value")

    page = client.get("/")
    for secret in ("lk_supersecret_value", "tok_supersecret_value",
                   "sha_supersecret_value"):
        assert secret not in page.text
    # The metadata table carries names and times only, never a credential.
    table = unclaimed_table(page.text)
    assert "lk_" not in table
    assert "token" not in table.lower()


