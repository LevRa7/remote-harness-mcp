"""The cabinet is mounted before the share catch-all and does not collide."""

from starlette.testclient import TestClient

import gateway


def test_account_path_is_the_cabinet_not_a_share_route():
    client = TestClient(gateway.app)
    response = client.get("/account")
    assert response.status_code == 200
    # Without MESH_ACCOUNT=1 the page says "not configured"; with it a login
    # redirect/page appears. Either way it must not be the share catch-all.
    assert "Remote Harness MCP" in response.text
    assert "share" not in response.headers.get("content-type", "")


def test_account_login_is_reachable():
    client = TestClient(gateway.app)
    response = client.get("/account/login", follow_redirects=False)
    assert response.status_code in (200, 303)


def test_account_does_not_advertise_wildcard_cors():
    client = TestClient(gateway.app)
    response = client.get("/account")
    assert "access-control-allow-origin" not in response.headers
