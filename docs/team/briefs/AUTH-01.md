# Task brief — AUTH-01: конфиг OIDC и валидация на старте

> Это пример task brief для Superpowers `subagent-driven-development`. Planner создаёт такой файл на каждую WBS-строку спринта до dispatch. Brief — единственный источник требований для implementer'а.

**WBS:** AUTH-01
**Owner:** `be-auth` (BE) · **Reviewer:** `sec-01` (SEC)
**Test:** `T-AUTH-01` · **Gate:** G3
**Зависимости:** нет

## Files

- Create: `account/__init__.py`
- Create: `account/config.py`
- Create: `account/app.py`
- Modify: `gateway.py` — mount `/account` only when `MESH_ACCOUNT=1`
- Create: `requirements-gateway.txt` — `jinja2`, `PyJWT[crypto]`
- Test: `tests/test_account_auth.py::test_missing_config_serves_not_configured`

## Interfaces

- `AccountConfig.from_env() -> AccountConfig`
  - поля: `client_id: str`, `client_secret: str`, `base_url: str`, `session_secret: bytes`, `enabled: bool`
  - `enabled` = `MESH_ACCOUNT == "1"`; если `enabled` и нет `client_id`/`client_secret`/`base_url`/`session_secret` — `ConfigError` с перечнем отсутствующих имён (без значений).
- `create_account_app(config: AccountConfig) -> Starlette`
- `gateway.py`: `app.mount("/account", create_account_app(...))` строго до catch-all `/{user}/{rest:path}`.

## Step 1 — failing test

```python
def test_missing_config_serves_not_configured(client, monkeypatch):
    monkeypatch.setenv("MESH_ACCOUNT", "1")
    monkeypatch.delenv("MESH_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("MESH_GOOGLE_CLIENT_SECRET", raising=False)
    resp = client.get("/account")
    assert resp.status_code == 200
    assert "не настроен" in resp.text
    assert "traceback" not in resp.text.lower()
    mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert mcp.status_code == 200
```

## Step 2 — verify red

```bash
python -m pytest -q tests/test_account_auth.py::test_missing_config_serves_not_configured
# Expected: FAIL — ModuleNotFoundError: account / 404
```

## Step 3 — implement

- `AccountConfig.from_env()` читает env, проверяет длину `session_secret` ≥ 32 B, не логирует значения.
- `create_account_app` при неполном конфиге отдаёт HTML «Кабинет не настроен» (200) и не роняет MCP.
- `gateway.py` монтирует `/account` только при `enabled`, до catch-all.

## Step 4 — verify green

```bash
python -m pytest -q tests/test_account_auth.py::test_missing_config_serves_not_configured
# Expected: PASS
```

## Step 5 — commit

```bash
git add account tests/test_account_auth.py gateway.py requirements-gateway.txt
git commit -m "feat(account): mount cabinet behind MESH_ACCOUNT with explicit config validation"
```

## Evidence

- `artifacts/AUTH-01/red.log`, `artifacts/AUTH-01/green.log`
- Reviewer проверяет: нет секретов в логах, `/mcp` жив, mount до catch-all, тест падал до реализации.
