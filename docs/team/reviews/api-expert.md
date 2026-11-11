# Ревью Wave 1 — API Expert (контракты, коды, версионирование)

**Роль:** API. **Область:** `/account/*`, `/account/api/*`, локальный `/v1/*`, туннель/`tools/call`, per-agent keys, привязка устройства, коды ошибок.
**Дата:** 2026-11-11. **База чтения:** `docs/wave-1-auth-account-log-settings.md` (далее W1), `docs/remote_harness_mcp_v2_plan.md` (V2), `docs/team/WBS.md`, `docs/team/AGENT_TEAM.md`, `gateway.py` (3452 стр.), `core/agent.py`, `core/mcp_tools.py`.
**Правило:** чужой код и документы не правились; проверено только чтением.

---

## 1. Вердикт

**NOT_READY.**

Контракты описаны на уровне «намерений»: у эндпоинтов нет кодов ответов, формата ошибок, лимитов, идемпотентности и пагинации, а два самых рискованных потока — claim устройства и per-agent ключи — противоречат текущему поведению `gateway.py` либо не имеют механизма доставки результата на устройство. Блокирующая тройка: конфликт `/v1/devices/pair` (создаёт новый узел и выдаёт токен) с `/account/api/claim` («привязывает существующий узел, новых ключей не выдаёт»), отсутствие доказательства владения устройством при claim (код выдаётся анонимно, а забирает его тоже анонимно), и коллизия пространства имён `/account` с catch-all-маршрутом шар. Формат ошибок сейчас текстовый → `mode_denied`/`node_paused`/`workspace_unavailable` не существуют как контракт и не могут обрабатываться клиентом. После фиксации 3 Blocker + 3 Critical (раздел 2) и внесения ~20 строк WBS (раздел 4) домен можно перевести в READY_WITH_FIXES; код писать до этого нельзя — контракт зафиксирует тесты, а не наоборот.

---

## 2. Findings

| ID | severity | область | проблема | доказательство | рекомендация | предложенный владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| API-01 | Blocker | pairing | W1 требует: `/v1/devices/pair` принимает действующий device token и «код привязывается к существующему узлу без выдачи новых ключей». Фактически эндпоинт анонимный, всегда создаёт заявку, а `approve` создаёт **новый** узел в registry и выдаёт новый token/link_key. Два контракта несовместимы, и в W1 не сказано, кто из них главный. | W1:150,616-630; `gateway.py:919-960`; `gateway.py:983-1030`; `gateway.py:3362-3365` | Один state machine `pair`, два входа: device-path (есть device token → attach к existing node) и anonymous-path (нет → create after claim). `/approve` и `/account/api/claim` вызывают одну внутреннюю функцию `claim_pair()` с разными `actor`; правила выдачи ключей — в одном месте; контракт ответов зафиксировать в OpenAPI. | API (контракт) + SEC (правила) |
| API-02 | Blocker | pairing/claim | Claim не доказывает владение устройством. Код выдаётся любому анонимному POST, credentials забирает любой, знающий код (`GET /v1/devices/pair/{code}`), а browser-claim лишь вводит код. Атакующий генерирует код у себя → убеждает пользователя ввести его в кабинете → узел атакующего становится устройством в тенанте пользователя (обратная кража). Плюс: в текущем виде по коду отдаётся device token (утечка ключа). | `gateway.py:939-960` (код без аутентификации), `gateway.py:963-980` (выдача `issued` с token без проверки), W1:150,510,616-630 | `POST /v1/devices/pair` возвращает `{code, pair_secret, fingerprint}`; `pair_secret` знает только устройство. Claim-экран показывает имя узла, платформу, fingerprint и IP, требует явного «Это моё устройство». `GET /pair/{code}` требует `pair_secret`; token выдаётся только устройству, которое создало заявку. | SEC |
| API-03 | Blocker | маршруты | `/account/*` попадает в catch-all `/{user}/{rest:path}` (шар-редирект), а `RESERVED_NAMES` не содержит `account`, `v1`, `pair`, `privacy`, `login`, `logout`. Узел, зарегистрированный под именем `account`, конфликтует с кабинетом; порядок монтажа `account` относительно catch-all нигде не задан. | `gateway.py:3379`, `gateway.py:296-303`, `gateway.py:771` (регистрация `[a-z0-9_-]`), W1:91 | Добавить `account`, `v1`, `pair`, `privacy`, `login`, `logout`, `static`, `assets` в `RESERVED_NAMES`; смонтировать `/account` **до** catch-all; тест «регистрация узла `account` → 400 reserved», «GET /account/login не уходит в шар-роут». | API + QA |
| API-04 | Critical | ошибки | Единого формата ошибки нет: 400 отдаёт `{"error":"Invalid JSON body"}` (строка без кода), 401 — `{"error":"unauthorized","message":…}`, 410 — `{"error":"registration_disabled","message":…}`, JSON-RPC — `-32601/-32602`, а ошибки инструментов — plain-text в `stderr`/`[Error] …`. Клиент не может отличить `mode_denied` от `node_offline`. | `gateway.py:759,768,773,2293,2349,2362`, `gateway.py:568-574`, `gateway.py:617-636`, `gateway.py:2878-2887` | Ввести RFC 9457 `application/problem+json` для HTTP и `{ok:false,error:{code,message,retryable,details,request_id}}` для MCP при `isError:true`; реестр кодов — раздел 3.8; сохранить `[Error]`-префикс для старых клиентов на один релиз. | API |
| API-05 | Critical | per-agent keys | Контракт ключа не определён: нет префикса в W1 (в WBS — `ak_`), нет порядка разрешения, не сказано, что будет с `user=` и со старыми `lk_`, не определён код отказа для отозванного агента, и не сказано, как связаны `registry.link_key` и `agents.key_sha256` (две записи одного секрета → рассинхрон при ротации). | W1:192-196; WBS AGT-03/06/07; `gateway.py:358-392` (`lk_`+32 hex, sha256), `gateway.py:1033-1062` (rotate link_key) | Формат `^ak_[A-Za-z0-9_-]{43}$` (32 B random), хранение sha256, разрешение одним helper'ом: `ak_`→agents→device→tenant; иначе `lk_`→default-агент; иначе device token. Ротация link_key и key `default` — одна функция записи. `user=` обязателен и должен совпадать с node_name устройства агента. | API + BE |
| API-06 | Critical | версионирование | V2 утверждает «мост определяет версию по `hello`», но `hello` не существует: WS-туннель принимает только ответы с `id`, а узел обрабатывает только `tools/call`. Приложение A W1 («устройство получает tenant_id/device_id следующим heartbeat») тоже нереализуемо: heartbeat — локальный файл, не кадр туннеля. | V2:218,1422-1438; `gateway.py:2523-2604` (только `id`), `core/agent.py:697-708` (только `tools/call`), `core/agent.py:309-369` (heartbeat — файл) | Зафиксировать: `hello` опционален, `proto=2`, молчание >3 с → legacy `proto=1`; статус claim устройство получает **поллингом** `GET /v1/devices/pair/{code}` с `pair_secret`, без нового push-канала. | A (Architect) + API |
| API-07 | Major | namespaces | `/v1/*` означает две разные вещи: публичный gateway API (`/v1/devices/*`) и локальный control API (`/v1/state`, `/v1/settings`, `/v1/usage`). `GET /v1/usage` в W1 указан и как журнал (`core/usage.py`), и как control API (`core/control.py`). `POST /v1/pause` дублирует `PATCH /v1/settings {paused}`. | W1:213,268,274-276; `gateway.py:3362-3369`; WBS CTL-06/07/08 | Локальный API — `127.0.0.1:<port>/v1/*` с явным префиксом хоста и отдельным документом; в W1 добавить строку «namespaces не пересекаются»; оставить один способ паузы (`PATCH /v1/settings`), `POST /v1/pause` не вводить. | API |
| API-08 | Major | account API | Не заданы: лимиты (`limit` max), сортировка (whitelist полей), фильтры, `Idempotency-Key` на создание агента (retry создаёт второго агента), `ETag`/`If-Match` (потеря обновлений при PATCH), `Cache-Control: no-store` на аутентифицированных JSON, `429`+`Retry-After`, `413/415`, `412`. | W1:155-163,192-196,488 (только «пагинация cursor») | Зафиксировать в OpenAPI: `limit` 1–100 (default 25), `cursor` opaque, `sort` из allowlist, `If-Match` на PATCH, `Idempotency-Key` на POST, `no-store`+`Vary: Cookie`, лимиты тела 256 КБ (`/claim` — 4 КБ). | API |
| API-09 | Major | локальный журнал | Контракт `GET /v1/usage` неполон: формат `since` не задан (`7d` vs ISO), нет `limit/cursor`, нет enum `status`, не задан `Content-Type` (`application/json` массив vs NDJSON), нет `Content-Disposition`/BOM для CSV, не задан ответ `DELETE /v1/usage` (204/200+count), нет границ `days` для `/summary`. | W1:212-216,274-276; WBS USE-09/CTL-08 | Явно: `since` и `until` — RFC 3339 UTC; `limit` 1–1000; сортировка `ts desc` по умолчанию; `status` ∈ `ok,error,denied,cancelled`; `GET /v1/usage` → `{items[],next_cursor}`; CSV — `text/csv; charset=utf-8` + BOM + `Content-Disposition: attachment`; `DELETE` → 200 `{deleted_lines, files_removed}`. | API + BE |
| API-10 | Major | claim коды | Коды claim противоречат друг другу: W1 «чужой код/повтор → 400 без деталей», W1:505 «чужая сущность — 404», WBS CLM-03 «409/400». Три разных ответа на один класс событий. | W1:152,505; WBS CLM-03,CLM-09 | Один код на все отрицательные исходы: `404 invalid_code` (нет/истёк/использован/чужой) + `audit meta.reason` внутри; `409 already_claimed` только для уже привязанного устройства; никогда не смешивать 400 и 404. | API + SEC |
| API-11 | Major | жизненный цикл | Нет эндпоинта отвязки/перепривязки устройства. `AUTH-18` («устройство отвязывается и отзывается») противоречит приёмке W1:544 («после выхода устройство остаётся на связи»). Не определено, что происходит с registry-записью при удалении аккаунта. | W1:544,205; WBS AUTH-18,CLM-08 | Добавить `DELETE /account/api/devices/{id}/claim` (unlink: `devices.tenant_id=NULL`, registry не трогаем, узел продолжает работать) и правило: удаление аккаунта удаляет БД-строки, но **не** отзывает device token узла (иначе пользователь теряет узел). | BE + SEC |
| API-12 | Major | OAuth | Нет точного redirect URI, правил вывода base URL, обработки ошибок Google, `next`-allowlist, logout-all, поведения при повторном логине другим аккаунтом. `MESH_ACCOUNT_BASE_URL` (W1:113) дублирует существующий `PUBLIC_BASE_URL` (`gateway.py:214-218`) — при расхождении `redirect_uri_mismatch` и «чужая» cookie. | W1:96,113,144-153,513,637; `gateway.py:214-218` | См. раздел 3.4: redirect URI = `<account_base>/account/auth/google/callback`; `MESH_ACCOUNT_BASE_URL` по умолчанию = `PUBLIC_BASE_URL`, при расхождении — отказ старта; ошибки Google → `303 /account/login?e=<code>`. | SEC + OPS |
| API-13 | Major | отзыв агента | Отзыв агента не может закрыть только его потоки: SSE-очереди и `active_sessions` индексируются по узлу (`user`), а не по агенту. «Активные сессии агента закрываются» из W1 нереализуемо без нового индекса. | W1:196; `gateway.py:277-294`, `gateway.py:125-135`, `gateway.py:2443-2460` | Ввести `agent_id` в `active_sessions`/`active_sse_subscribers` (ключ `(user, agent_id)`) до Sprint 2; тест «отзыв агента A закрывает поток A, поток B жив». | BE |
| API-14 | Major | CORS/CSRF | Глобальный `CORSMiddleware` с `allow_origins=["*"]` применяется и к `/account/api/*`; CSRF-защита опирается на Origin, но конфигурация CORS об этом не знает и «разрешает всё» на бумаге. | `gateway.py:3382-3390`; W1:148,501 | Исключить `/account/*` из wildcard-CORS (отдельный middleware/route-scope), не отражать `Origin` для `/account/api/*`, добавить `Vary: Origin`. | SEC |
| API-15 | Minor | доверие к IP | `client_ip()` берёт первый `X-Forwarded-For` без доверенного списка прокси → обход rate limit claim/логина и подмена IP в аудите. Rate limit — in-memory, на процесс. | `gateway.py:726-730`, `gateway.py:736-750` | `MESH_TRUSTED_PROXIES` (CIDR allowlist) иначе брать `request.client.host`; лимиты по `account_id`+IP; для multi-worker — общий store (SQLite/Redis) или явно «1 процесс». | OPS + SEC |
| API-16 | Minor | мёртвая ссылка | `POST /v1/devices/pair` возвращает `pair_url: /pair?code=…`, но маршрута `/pair` нет (односегментный путь не матчится catch-all). | `gateway.py:954`, `gateway.py:3355-3380` | Либо добавить `/pair` (страница), либо убрать поле из ответа; контракт не должен обещать несуществующий URL. | API |
| API-17 | Minor | версия сервера | `serverInfo.version` жёстко `"1.0.0"`, `capabilities.tools.listChanged=false`, хотя scopes меняют набор инструментов. | `gateway.py:1423-1433`; W1:281 | Отдавать реальную версию (`core/version.py`); либо `listChanged:true` + `notifications/tools/list_changed`, либо документировать «новый набор — только после `initialize`». | API |
| API-18 | Minor | документы | Два неймспейса API в документах: W1 `/account/api/*`, V2 E4 `/v1/account/*`. Оба не могут существовать одновременно. | W1:155-163; V2:653-667 | Ruling: Wave 1 — только `/account/api/*` (cookie+CSRF); `/v1/account/*` — edge, Wave 3; в V2 добавить пометку «эскиз, не контракт». | P (Planner) |

---

## 3. Ответы на вопросы ревью

### 3.1. (Q1) Полнота контрактов `/account/*` и локального `/v1/*`

Матрица полноты (✅ есть в W1/WBS, ⚠ частично, ❌ нет):

| Группа | методы | коды | тело ошибки | Content-Type | idempotency | пагинация | сортировка | фильтры | ETag/кеш | лимиты |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| auth (`start/callback/logout`) | ✅ | ⚠ | ❌ | ❌ | ❌ | — | — | — | ❌ | ⚠ |
| claim | ✅ | ⚠ (противоречивы) | ❌ | ❌ | ❌ | — | — | — | ❌ | ⚠ |
| devices CRUD | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠ (только «cursor») | ❌ | ❌ | ❌ | ❌ |
| agents CRUD | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| usage (кабинет) | ⚠ | ❌ | ❌ | ❌ | — | ❌ | ❌ | ⚠ | ❌ | ❌ |
| usage (локальный) | ⚠ | ❌ | ❌ | ❌ | — | ❌ | ❌ | ⚠ | — | ❌ |
| settings/state (локальный) | ⚠ | ❌ | ❌ | ❌ | ❌ | — | — | — | ❌ | ❌ |
| MCP (`/sse`,`/mcp`,`/messages`) | ✅ | ⚠ | ❌ | ⚠ | — | — | — | — | — | ⚠ |

Вывод: контракты **неполны** по всем осям, кроме списка путей. Минимум для старта Sprint 1: раздел 7 (таблица + OpenAPI) + раздел 8 (реестр кодов). Всё остальное — производные.

### 3.2. (Q2) Аутентификация device-запросов внутри gateway

**Решение: отдельный shared-secret/подпись в Wave 1 не нужен** — `account/` живёт в процессе `gateway.py`, межсервисной границы нет. Достаточно существующего device token (sha256 в registry) как единственного device-credential. Механизм:

| Эндпоинт | Credential | Как резолвится | Отказ |
| :--- | :--- | :--- | :--- |
| `POST /v1/devices/pair` | нет (anonymous) или device token (re-pair) | если header есть и валиден → attach к `user` | `401 invalid_token` при невалидном предъявленном токене |
| `GET /v1/devices/pair/{code}` | `pair_secret` (тело/заголовок `X-Pair-Secret`) | `pending_pairs[code].secret` (constant-time) | `404 invalid_code` |
| `POST /account/api/usage/ingest` | `Authorization: Bearer <device token>` | `token_matches_device(entry, token)` → `entry` → `devices.node_name` → `tenant_id` | `401 invalid_token`; `lk_`/`ak_` → `403 device_credential_required` |
| `GET /v1/devices/pair/{code}` (статус для трея) | `pair_secret` | то же | `404` |
| `POST /v1/devices/{user}/rotate*`, `/revoke` | device token или admin | как сейчас | как сейчас |

Правила, которые надо зафиксировать:
1. **Identity выводится только из credential.** `device_id`/`tenant_id`/`node_name` в теле игнорируются; если пришли и не совпали → `400 identity_mismatch` (не «тихо перезаписать»).
2. **`link_key` (`lk_`) не даёт device-скоупа.** Ingest — только device token; иначе любой агент может переписать суточные счётчики всего устройства.
3. **Порядок:** device token → (при отсутствии) agent key → link key. Ingest не принимает `ak_`.
4. **Replay:** ingest идемпотентен по `(device_id, agent_id, day, tool)`; повтор безопасен, HMAC не нужен. Webhook-подписи (HMAC-SHA256 над `method+path+body+ts`, окно ±300 с) — **Wave 3**, вместе с выделением `account/` в отдельный сервис; сейчас это принятый риск, записать в ADR-005.
5. **Unclaimed device:** `pair`-статус работает, `ingest` → `409 device_unclaimed` (не 401: credential валиден, состояние не то).
6. **Внутренние вызовы** (кабинет читает usage, claim пишет registry) — прямые функции `account.*`, без HTTP и без второго секрета.

### 3.3. (Q3) State machine привязки устройства

Сейчас в коде: код → `pending_pairs[code]` → `approve` (admin token) создаёт **новый** узел + token → устройство забирает `GET /pair/{code}` (единожды, анонимно). В W1: браузер вводит код, устройство узнаёт о привязке «следующим heartbeat». Совместить так:

```
                 POST /v1/devices/pair {hostname, os, arch[, device_token]}
                          │ 201 {code, pair_secret, expires_in, fingerprint}
                          ▼
   ┌──────────┐  TTL 10 мин   ┌──────────┐
   │  issued  │──────────────▶│ expired  │
   └────┬─────┘               └──────────┘
        │ POST /account/api/claim {code}   (сессия+CSRF, браузер)
        │ → devices row создаётся ТОЛЬКО здесь; existing node attach, если был device_token
        ▼
   ┌──────────┐  GET /pair/{code} + pair_secret  (устройство, поллинг 2–5 с)
   │ claimed  │──────────────────────────────────▶ collected (код уничтожен)
   └────┬─────┘        200 {status, device_id, tenant_id, node_name}
        │ TTL истёк без поллинга
        ▼
   ┌──────────┐
   │ expired  │  кабинет показывает «ожидает устройство» (orphan, W1:402)
   └──────────┘
```

| Переход | Кто | Условие | Ответ | Побочные эффекты |
| :--- | :--- | :--- | :--- | :--- |
| ∅ → issued | устройство | rate limit 20/мин/IP | `201 {code, pair_secret, expires_in:600}` | `pair_started` в аудит; `pair_secret` — 32 B, только устройству |
| issued → claimed | браузер | сессия + CSRF + код жив + не claimed | `200 {device:{id,name,platform}}` | `devices.tenant_id`, `registry[node].device_id/tenant_id` — одной транзакцией; `claim_succeeded` |
| issued → expired | время | `now > expires_at` | — | `claim_expired` (lazy purge) |
| claimed → collected | устройство | `pair_secret` совпал | `200 {status:"claimed", device_id, tenant_id}` | код и секрет уничтожаются; `claim_collected` |
| issued → voided | устройство/кабинет | `DELETE /v1/devices/pair/{code}` | `204` | `claim_cancelled` |
| claimed → claimed (2-й) | браузер | повтор | `409 already_claimed` / `404` | `claim_failed` |
| claimed → expired | время | не собран за TTL | — | устройство остаётся непривязанным, в кабинете «требует восстановления» |

Обязательные правила:
- **Кто создаёт код:** только устройство (оно показывает его на экране). Браузер код не создаёт — иначе claim не доказывает ничего.
- **Кто подтверждает:** только залогиненный человек; claim-страница обязана показать fingerprint/платформу/IP и потребовать checkbox.
- **Кто получает устройство:** оно получает только `device_id/tenant_id`; **никаких новых ключей**, если device token уже предъявлен. Если заявка анонимная — `approve` (admin) или claim выдают ключи один раз, как сейчас.
- **Наследие:** узел до v2 приходит с device token → attach; token/link_key не меняются (W1:151, WBS CLM-07).
- **Отвязка:** `DELETE /account/api/devices/{id}/claim` (нет в W1 — см. API-11).
- **Гонка:** `UPDATE … WHERE claimed_at IS NULL` в одной транзакции SQLite; ровно один `200`.

### 3.4. (Q4) OAuth: что зафиксировать точно

| Параметр | Значение |
| :--- | :--- |
| redirect URI (prod) | `https://<MESH_ACCOUNT_BASE_URL host>/account/auth/google/callback` — без trailing slash, без query; ровно эта строка в Google Console |
| redirect URI (dev) | `http://127.0.0.1:<port>/account/auth/google/callback` (Google разрешает loopback-http); отдельный OAuth client или отдельный URI в том же client |
| base URL | `MESH_ACCOUNT_BASE_URL` default = `PUBLIC_BASE_URL`; если задан иначе — старт отклоняется (иначе `redirect_uri_mismatch` + cookie на чужом хосте) |
| scopes | `openid email profile`; `access_type=online`, `prompt=select_account`; refresh token **не запрашиваем и не храним** |
| PKCE | `code_challenge_method=S256`, verifier 43–128 символов, хранится только в login-сессии |
| state/nonce | 32 B random каждый; `state` одноразовый, TTL 10 мин; хранится server-side (таблица `login_attempts`) либо в `__Host-rhm_oauth`; сравнение constant-time; удаляется на любом исходе |
| cookie сессии (prod) | `__Host-rhm_session`; `Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000`; без `Domain` |
| cookie сессии (dev) | `rhm_dev_session` без `Secure`, только при `MESH_AUTH_MODE=dev` и loopback; при не-loopback — 403 |
| CSRF | double-submit `__Host-rhm_csrf` (не HttpOnly) + `X-CSRF-Token`; плюс проверка `Origin`/`Referer` == base URL и `Sec-Fetch-Site != cross-site` на всех POST |
| logout | `POST /account/logout` + CSRF → удалить строку сессии, очистить cookie, `204`; идемпотентно; `?all=1` — все сессии (AUTH-14) |
| `next` | только путь `^/account/(?!/account/auth/)[A-Za-z0-9._~/?=&%-]*$`; запрещены `//`, `\`, `%2f%2f`, абсолютные URL, `/account/auth/*`; иначе `400 invalid_next` (не молчаливый игнор) |
| ошибки Google | `error=access_denied` → `303 /account/login?e=access_denied`; `invalid_client`/`redirect_uri_mismatch` → страница «кабинет не настроен» + ERROR-лог без секретов; 4xx от token endpoint → `502 oauth_exchange_failed`; JWKS недоступен → `503 jwks_unavailable`, кэш не сбрасывается; таймауты 5 с (token) / 10 с (JWKS) |
| повторный логин | тот же `sub` при живой сессии → ротация `session_id` (fixation), ответ `303`; другой `sub` → `409 account_switch_required` (явное «сменить аккаунт»); **слияния по email нет** — только `google_sub` |
| удаление аккаунта | удалить сессии/агентов, `devices.tenant_id=NULL`; device token узла **не** отзывать (см. API-11) |

Не учтено в W1: (1) `Referrer-Policy: no-referrer` на auth-страницах (иначе `state` утекает через Referer внешних ресурсов); (2) `X-Forwarded-Proto`/`Host` за nginx — без доверенного прокси cookie окажется без `Secure`; (3) параллельные вкладки с двумя `state` (нужен список in-flight, а не один слот); (4) `GET /account/logout` должен быть `405`, иначе CSRF-выход; (5) `POST /account/api/claim` при истёкшей сессии → `401`, а не redirect (htmx не умеет в 303).

### 3.5. (Q5) Per-agent keys

- **Формат:** `ak_` + 43 символа base64url (32 B энтропии), regex `^ak_[A-Za-z0-9_-]{43}$`; legacy `lk_` + 32 hex остаётся. В `agents.key_sha256` — SHA-256, plaintext не хранится и не логируется (WBS AGT-03/04).
- **URL:** без изменений формы — `https://<host>/sse?user=<node>&token=ak_…`. Параметр `agent=` **не нужен**: токен уже резолвится в агента, а лишний параметр создаёт соблазн `?agent=` в обход. Если появится позже — валидировать `agent == resolved.agent_id`, иначе `400 agent_mismatch`.
- **Разрешение** (один helper, порядок фиксирован): `ak_` → `agents.key_sha256` → `devices` → `tenant`; если не найдено → `lk_` → registry `link_key` → агент `default` (ленивое создание); если не найдено → device token (для header-формы и legacy-query по текущим правилам `allow_legacy_query`). `user=` обязателен и должен совпасть с `devices.node_name`; иначе `404 node_not_found` (не 403 — не раскрываем чужие узлы).
- **Отзыв:** `revoked_at`; следующий вызов → `401 agent_revoked` + `WWW-Authenticate: Bearer error="invalid_token"`; параллельно закрываются SSE-потоки именно этого агента (нужен индекс `(user, agent_id)`, см. API-13); in-flight — по политике `kill_on_downgrade`.
- **Совместимость:** старые ссылки `lk_…` работают без перевыпуска и резолвятся в агента `default`; `rotate link` обновляет `registry.link_key` **и** `agents(default).key_sha256` одной функцией; отзыв `default` не должен ломать других агентов (WBS AGT-07).
- **Коды:** нет токена → `401 missing_credential`; битый/неизвестный → `401 invalid_token`; отозванный → `401 agent_revoked`; device token в query без `allow_legacy_query` → текущий `401` с текстом `credential_denial` (`gateway.py:418-429`) сохранить (клиенты на него завязаны).
- **Отдельный заголовок:** для MCP — не нужен (Gemini/Claude требуют URL). Для `/v1/*` device API — обязательный `Authorization: Bearer`. Дополнительный `X-RHM-Agent` не вводить: identity берётся из credential, иначе появляется второй, непроверяемый источник.

### 3.6. (Q6) Версионирование без разрыва старых узлов и клиентов

Факты: `initialize` уже договаривается о `protocolVersion` и эхом отвечает на любую дату (`gateway.py:1385-1403`); `serverInfo.version` — константа `1.0.0` (`gateway.py:1430-1433`); в туннеле нет handshake (`gateway.py:2579-2589` принимает только ответы с `id`; `core/agent.py:702` — только `tools/call`).

Минимальный контракт:
1. **`hello` — опциональный первый кадр узел→мост:** `{type:"hello", v:1, proto:2, node_version, device_id?, capabilities:[], mode_default}`. Мост: нет `hello` за 3 с → `proto=1` (legacy), ничего не требует; пришёл `hello` → сохранить caps, ответить `welcome {proto:2, server_time, limits}` (без блокировки вызовов). Старый мост молча игнорирует кадр без `id` — проверено логикой `if req_id and req_id in pending`; старый узел молча игнорирует `welcome`. Обе стороны совместимы без флагов.
2. **`tools/call` (мост→узел):** добавить `ctx{agent_id, mode, scopes, workspace, proto:2}`. **Критично:** при отсутствии `ctx` узел обязан работать в текущем глобальном режиме (`MESH_READ_ONLY`), а не в `read_only`. Иначе новый узел + старый мост отберёт запись у существующих пользователей. WBS MOD-06 («старый узел без ctx получает дефолт») надо переформулировать: дефолт = текущее поведение, не `read_only`.
3. **Клиент→мост:** ничего нового в `tools/call` не добавлять; контекст вычисляет мост по credential. Для расширяемости — `params._meta` (стандартное поле MCP), а не новые top-level ключи.
4. **`initialize.result`:** добавить `capabilities.experimental.rhm = {proto:2, features:["ctx","agent_keys","usage"]}` и настоящий `serverInfo.version`. Старые клиенты игнорируют неизвестные поля.
5. **Фиксация совместимости:** golden-файл `tools/list` для legacy-режима (снимок 0.4.8) + тест «новый узел к старому мосту» / «старый узел к новому мосту» (оба — без `ctx`).
6. **Forward-compat правило:** неизвестные кадры/поля игнорируются, неизвестные `error.code` клиент показывает как `error` с текстом; `code` — стабильный, `message` — локализуемый, они не взаимозаменяемы.

### 3.7. (Q7) Ключевые эндпоинты: контракт

| # | Метод и путь | Auth | Request | Success | Ошибки | Идемпотентность/заголовки |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `POST /account/auth/google/start` | нет (rate limit) | `?next=` | `303` → Google | `400 invalid_next`, `429` | `no-store`, `Referrer-Policy: no-referrer` |
| 2 | `GET /account/auth/google/callback` | oauth-cookie (state/nonce/verifier) | `code,state` | `303 /account/` | `400 invalid_state`, `401 oauth_failed`, `502 oauth_exchange_failed`, `503 jwks_unavailable` | одноразово; cookie удаляется всегда |
| 3 | `GET /account/api/session` | cookie | — | `200 {account,tenant,csrf_token}` | `401 unauthorized` | `ETag: W/"s-<ver>"`, `no-store`, `Vary: Cookie` |
| 4 | `POST /account/logout` | cookie+CSRF | `{all?:bool}` | `204` | `401`, `403 csrf_failed` | идемпотентно |
| 5 | `POST /account/api/claim` | cookie+CSRF | `{code}` | `200 {device}` | `401`, `403`, `404 invalid_code`, `409 already_claimed`, `429` | тело ≤ 4 КБ; single-use |
| 6 | `GET /account/api/devices` | cookie | `limit,cursor,status,q,sort` | `200 Page<Device>` | `401`, `422 validation_error` | `ETag` (версия списка), `no-store` |
| 7 | `GET /account/api/devices/{id}` | cookie | — | `200 Device` | `401`, `404 not_found` | strong `ETag` |
| 8 | `PATCH /account/api/devices/{id}` | cookie+CSRF+`If-Match` | `{display_name,mode_default}` | `200 Device` | `400`, `404`, `409 conflict`, `412 precondition_failed`, `422` | новый `ETag` |
| 9 | `DELETE /account/api/devices/{id}` | cookie+CSRF | `{confirm_name}` | `204` | `401`, `404`, `409 has_active_agents`, `422` | идемпотентно |
| 10 | `DELETE /account/api/devices/{id}/claim` | cookie+CSRF | — | `200 {unlinked:true}` | `401`, `404` | идемпотентно (API-11) |
| 11 | `GET /account/api/devices/{id}/agents` | cookie | `limit,cursor` | `200 Page<Agent>` | `401`, `404` | `no-store` |
| 12 | `POST /account/api/devices/{id}/agents` | cookie+CSRF+`Idempotency-Key` | `{label,mode,scopes,workspace}` | `201 {agent,key}` (key — один раз) | `400`, `404`, `409 agent_limit`, `422`, `429` | повтор с тем же ключом → тот же `201` |
| 13 | `PATCH /account/api/agents/{id}` | cookie+CSRF+`If-Match` | `{label,mode,scopes,workspace}` | `200 Agent` | `400`, `404`, `412`, `422` | |
| 14 | `DELETE /account/api/agents/{id}` | cookie+CSRF | — | `204` | `401`, `404` | идемпотентно; закрывает потоки агента |
| 15 | `POST /account/api/usage/ingest` | Bearer **device token** | `{day,buckets:[{agent_id,tool,ok,err,denied,duration_ms_sum,bytes_sum}]}` | `202 {accepted}` | `400 identity_mismatch`, `401 invalid_token`, `403 device_credential_required`, `409 device_unclaimed`, `413`, `415`, `429` | идемпотентно по `(device,agent,day,tool)` |
| 16 | `GET /account/api/usage` | cookie | `from,to,device,agent,tool,status,granularity,limit,cursor` | `200 {series,totals,top_tools}` | `401`, `422` (`from>to`, окно > 90 дней) | `no-store` |

Локальный control API (только `127.0.0.1`, `control.token`, `Host`/`Origin` проверяются):

| Метод и путь | Request | Success | Ошибки |
| :--- | :--- | :--- | :--- |
| `GET /v1/state` | — | `200 {version,gateway,status,mode,workspace,agents,active_calls}` | `401`, `403 origin_denied` |
| `GET /v1/events` | `Accept: text/event-stream` | `200` SSE (`state/call/approval/error`) | `401`, `403` |
| `PATCH /v1/settings` | `{default_mode,workspace,paused,autostart,pulse}` | `200` новое состояние | `400` (поле+причина), `401`, `403`, `422` |
| `GET /v1/usage` | `since,until,limit,cursor,tool,status,agent` | `200 {items,next_cursor}` | `400`, `401` |
| `GET /v1/usage/summary` | `days=1..90` | `200 {counts,p50,p95,bytes,top_tools,by_agent}` | `400`, `401` |
| `GET /v1/usage/export.csv` | те же фильтры | `200 text/csv` (+`Content-Disposition`) | `400`, `401` |
| `DELETE /v1/usage` | `{confirm:true}` | `200 {deleted_lines}` | `400`, `401` |

OpenAPI 3.1 — фрагмент (достаточно как источник истины для схем и contract-тестов):

```yaml
openapi: 3.1.0
info: {title: RHM Account API, version: "1.0"}
servers: [{url: "https://{host}", variables: {host: {default: "mesh.example.com"}}}]
security: [{sessionCookie: []}]
components:
  securitySchemes:
    sessionCookie: {type: apiKey, in: cookie, name: "__Host-rhm_session"}
    csrf: {type: apiKey, in: header, name: "X-CSRF-Token"}
    deviceToken: {type: http, scheme: bearer}
  headers:
    ETag: {schema: {type: string}}
  schemas:
    Problem:
      type: object
      required: [type, title, status, code]
      properties:
        type: {type: string, format: uri}
        title: {type: string}
        status: {type: integer}
        code: {$ref: "#/components/schemas/ErrorCode"}
        detail: {type: string, description: "локализованный текст, ru/en по Accept-Language"}
        instance: {type: string}
        retryable: {type: boolean}
        request_id: {type: string}
        errors: {type: array, items: {type: object, properties: {field: {type: string}, reason: {type: string}}}}
    ErrorCode:
      type: string
      enum: [unauthorized, invalid_token, agent_revoked, csrf_failed, not_found, invalid_code,
             already_claimed, code_expired, conflict, validation_error, precondition_failed,
             rate_limited, payload_too_large, unsupported_media_type, device_unclaimed,
             device_credential_required, identity_mismatch, oauth_failed, oauth_exchange_failed,
             jwks_unavailable, internal]
    Device:
      type: object
      required: [id, node_name, display_name, status, created_at]
      properties:
        id: {type: string, pattern: "^dev_"}
        node_name: {type: string}
        display_name: {type: string, maxLength: 64}
        platform: {type: string, enum: [windows, macos, linux, android, unknown]}
        arch: {type: string}
        version: {type: string}
        mode_default: {type: string, enum: [read_only, agent]}
        workspace: {type: [string, "null"]}
        status: {type: string, enum: [active, revoked, orphaned]}
        online: {type: boolean}
        last_seen_at: {type: [string, "null"], format: date-time}
    Agent:
      type: object
      required: [id, device_id, label, mode, scopes]
      properties:
        id: {type: string, pattern: "^ag_"}
        device_id: {type: string}
        label: {type: string, maxLength: 64}
        client_type: {type: string}
        mode: {type: string, enum: [read_only, agent]}
        scopes: {type: array, items: {type: string}}
        workspace: {type: [string, "null"]}
        last_used_at: {type: [string, "null"], format: date-time}
        revoked_at: {type: [string, "null"], format: date-time}
    Page:
      type: object
      required: [items, next_cursor]
      properties:
        items: {type: array}
        next_cursor: {type: [string, "null"]}
    UsageBucket:
      type: object
      required: [agent_id, tool, ok, err, denied]
      properties:
        agent_id: {type: string}
        tool: {type: string}
        ok: {type: integer, minimum: 0}
        err: {type: integer, minimum: 0}
        denied: {type: integer, minimum: 0}
        duration_ms_sum: {type: integer, minimum: 0}
        bytes_sum: {type: integer, minimum: 0}
  responses:
    Problem: {description: "RFC 9457", content: {"application/problem+json": {schema: {$ref: "#/components/schemas/Problem"}}}}
paths:
  /account/api/claim:
    post:
      summary: Привязать устройство к аккаунту по коду
      security: [{sessionCookie: []}, {csrf: []}]
      requestBody:
        required: true
        content: {"application/json": {schema: {type: object, required: [code],
          properties: {code: {type: string, pattern: "^[A-Z2-9]{4}-[A-Z2-9]{4}$"}}}}}
      responses:
        "200": {description: Привязано, content: {"application/json": {schema: {$ref: "#/components/schemas/Device"}}}}
        "401": {$ref: "#/components/responses/Problem"}
        "403": {$ref: "#/components/responses/Problem"}
        "404": {$ref: "#/components/responses/Problem"}
        "409": {$ref: "#/components/responses/Problem"}
        "429": {description: "Retry-After", headers: {Retry-After: {schema: {type: integer}}}}
  /account/api/usage/ingest:
    post:
      summary: Суточные агрегаты с устройства
      security: [{deviceToken: []}]
      requestBody:
        required: true
        content: {"application/json": {schema: {type: object, required: [day, buckets],
          properties:
            day: {type: string, format: date}
            buckets: {type: array, maxItems: 500, items: {$ref: "#/components/schemas/UsageBucket"}}}}}
      responses:
        "202": {description: Принято}
        "400": {$ref: "#/components/responses/Problem"}
        "401": {$ref: "#/components/responses/Problem"}
        "403": {$ref: "#/components/responses/Problem"}
        "409": {$ref: "#/components/responses/Problem"}
        "413": {$ref: "#/components/responses/Problem"}
  /account/api/devices:
    get:
      parameters:
        - {in: query, name: limit, schema: {type: integer, minimum: 1, maximum: 100, default: 25}}
        - {in: query, name: cursor, schema: {type: string}}
        - {in: query, name: status, schema: {type: string, enum: [active, revoked, orphaned]}}
        - {in: query, name: sort, schema: {type: string, enum: [created_at, -created_at, last_seen_at, -last_seen_at, display_name]}}
      responses:
        "200": {headers: {ETag: {$ref: "#/components/headers/ETag"}},
          content: {"application/json": {schema: {$ref: "#/components/schemas/Page"}}}}
        "401": {$ref: "#/components/responses/Problem"}
        "422": {$ref: "#/components/responses/Problem"}
```

### 3.8. (Q8) Неоднозначности кодов ошибок и единый формат

| Код | Слой | HTTP | MCP | retryable | Когда |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `unauthorized` / `invalid_token` / `agent_revoked` | gateway | 401 + `WWW-Authenticate` | — | нет | credential отсутствует/не распознан/отозван |
| `csrf_failed` | account | 403 | — | нет | нет/чужой CSRF, чужой Origin |
| `not_found` / `invalid_code` | account | 404 | — | нет | чужая сущность, неизвестный/истёкший код |
| `already_claimed` / `conflict` | account | 409 | — | нет | гонка claim, конфликт имён |
| `validation_error` | любой | 422 (+`errors[]`) | — | нет | невалидные поля/фильтры |
| `precondition_failed` | account | 412 | — | нет | устаревший `If-Match` |
| `rate_limited` | любой | 429 + `Retry-After` | — | да | лимит |
| `payload_too_large` / `unsupported_media_type` | любой | 413 / 415 | — | нет | тело/тип |
| `mode_denied` | мост/узел | **200** | `isError:true` | нет | режим запрещает инструмент |
| `scope_denied` | мост | 200 | `isError:true` | нет | группа scopes не выдана |
| `approval_required` | узел | 200 | `isError:true` + `details.approval_id` | нет | ждёт решения (Wave 2) |
| `approval_denied` | узел | 200 | `isError:true` | нет | человек отказал |
| `node_paused` | узел | 200 | `isError:true` | **да**, после снятия паузы | пауза в трее |
| `node_offline` | мост | 200 | `isError:true` | **да**, после реконнекта | нет туннеля (`bridge_unavailable` — только для self-health) |
| `workspace_unavailable` | узел | 200 | `isError:true` | нет (нужен выбор папки) | папка исчезла/нет прав |
| `workspace_not_allowed` | узел | 200 | `isError:true` | нет | путь вне allowlist (нарушение политики, аудит) |
| `invalid_args` / `unknown_tool` | мост | 200 / JSON-RPC `-32602`/`-32601` | `isError` / `error` | нет | контракт инструмента |
| `internal` | любой | 500 | `isError:true` | возможно | непредвиденное, без деталей наружу |

Правила устранения неоднозначностей:
1. **`mode_denied` vs `scope_denied`:** если запрет вызван и режимом, и scope — отдавать `mode_denied` (это то, что пользователь может исправить в UI), а `details{required_scopes, mode}` — для диагностики.
2. **`node_paused` vs `node_offline`:** пауза = узел ответил; офлайн = туннеля нет. Никогда не отдавать 503 на `tools/call` за офлайн узла — клиент сбросит сессию; сейчас узел офлайн приходит текстом в `stderr` (`gateway.py:568-574`) — заменить на `code:node_offline`, текст сохранить.
3. **`workspace_unavailable` vs `workspace_not_allowed`:** «нет папки» ≠ «нельзя в эту папку». Первое лечится пользователем, второе — инцидент безопасности и запись в аудит.
4. **`bridge_unavailable` убрать из клиентского контракта:** клиент не может ничего сделать с «мостом»; наружу — `node_offline` + `request_id`; `bridge_unavailable` оставить только в `/health`.
5. **Единый конверт:** HTTP — `application/problem+json` (поля `type,title,status,code,detail,instance,retryable,request_id,errors[]`); MCP — `result.isError=true` + `structuredContent.error{code,message,retryable,request_id}` + текст `[Error] <локальный текст> (<code>)` для клиентов, читающих только текст. `code` стабилен, локализуется только `detail`/`message`.
6. **Один код = один владелец:** коды 401/403/404/409/412/422/429 — account/gateway; `mode_denied`/`node_paused`/`workspace_*` — узел; `node_offline` — мост. Реестр кодов — в OpenAPI (`components.schemas.ErrorCode`) и в документе, один источник.

---

## 4. Пропущенные детали (с WBS-ID)

| WBS-ID (новый) | Что добавить | Почему обязательно |
| :--- | :--- | :--- |
| REV-CLM-11 | `pair_secret`: генерация, передача, проверка, уничтожение | без него claim не доказывает владение устройством (API-02) |
| REV-CLM-12 | Контракт статуса `GET /v1/devices/pair/{code}`: `pending/claimed/expired`, поллинг 2–5 с, без данных аккаунта | замена несуществующего «heartbeat» из W1:628 |
| REV-CLM-13 | `DELETE /account/api/devices/{id}/claim` (unlink) + правило «узел продолжает работать» | W1:544 противоречит WBS AUTH-18 |
| REV-AUTH-19 | Точный redirect URI, вывод base URL из `PUBLIC_BASE_URL`, запрет расхождения на старте | иначе `redirect_uri_mismatch` в проде |
| REV-AUTH-20 | Cookie dev/prod, `Referrer-Policy`, `GET logout→405`, logout-all | утечка `state`, fixation |
| REV-AUTH-21 | Маппинг ошибок Google и таймауты (5/10 с), коды `502/503` | иначе 500 и «молчаливый» вход |
| REV-AGT-11 | Контракт `ak_`/`lk_`/device: regex, порядок, `user=`, коды, одна функция ротации | иначе рассинхрон `registry.link_key` и `agents` |
| REV-AGT-12 | Индекс `(user, agent_id)` для потоков/сессий; закрытие только своего агента | W1:196 нереализуемо без него |
| REV-USE-15 | Полный контракт `GET /v1/usage` (`since` RFC 3339, `limit`, `cursor`, сортировка, `status` enum, CSV-заголовки, `DELETE`-ответ) | иначе трей и CLI разойдутся |
| REV-USE-16 | Контракт ingest: только device token, `day` в UTC, ≤500 бакетов, ≤256 КБ, неизвестный tool, `identity_mismatch` | иначе отравление статистики |
| REV-CAB-15 | OpenAPI + `problem+json` + коды 404/409/412/413/415/429 с `Retry-After` | базис contract-тестов |
| REV-CAB-16 | Пагинация/сортировка/фильтры: whitelist, `limit` 1–100, opaque cursor, стабильный порядок `(created_at,id)` | W1:488 без параметров |
| REV-CAB-17 | `ETag`/`If-Match` на PATCH, `Cache-Control: no-store`, `Vary: Cookie` | потеря обновлений, кеш приватных данных |
| REV-CAB-18 | `Idempotency-Key` на `POST …/agents` (TTL 24 ч) | retry создаёт второго агента |
| REV-CTL-11 | Разделение namespaces: публичный `/v1/devices/*` vs `127.0.0.1/v1/*`; в WBS CTL-10 `Host`/`Origin` уже есть, в плане Sprint 3 — нет: синхронизировать (Origin+Host+порт-файл) и исключить `/account` из wildcard-CORS | DNS rebinding + путаница в доках |
| REV-CTL-12 | Единственный способ паузы (`PATCH /v1/settings`), `POST /v1/pause` не вводить | дубль из W1:268/275 |
| REV-OPS-09 | `hello`/`welcome` опционально, legacy `proto=1` при молчании 3 с, golden `tools/list` | V2:218 утверждает несуществующее |
| REV-OPS-10 | Реестр кодов ошибок + единый конверт + golden-тесты на каждый код | API-04 |
| REV-SEC-09 | `RESERVED_NAMES` += `account,v1,pair,privacy,login,logout`; порядок маршрутов | API-03 |
| REV-SEC-10 | `MESH_TRUSTED_PROXIES`; `client_ip` только от доверенного прокси | обход rate limit, ложный аудит |
| REV-SEC-11 | Исключить `/account/*` из wildcard-CORS; `Vary: Origin` | CSRF-граница |
| REV-OPS-11 | Rate limit по `(account, ip, device)` с учётом multi-worker (общий store или «1 процесс») | claim/ingest под нагрузкой |

---

## 5. Пропущенные тесты (contract)

Все — автоматические (A), кроме отмеченных M.

| ID | Что | Шаги | Ожидание |
| :--- | :--- | :--- | :--- |
| T-API-01 | OpenAPI ↔ реализация (A) | `schemathesis run` по `/account/api/*` со спекой из репозитория | нет `5xx`, нет ответов вне схемы, все задокументированные коды достижимы |
| T-API-02 | Конверт ошибки (A) | 401/403/404/409/412/422/429 на 5 эндпоинтах | `application/problem+json`, обязательные поля, `code` из enum, `request_id` есть |
| T-API-03 | CSRF (A) | POST без токена / чужой токен / `Origin: https://evil` / `Sec-Fetch-Site: cross-site` | `403 csrf_failed`, сайд-эффектов нет |
| T-API-04 | IDOR (A) | два аккаунта, чужой `device_id`/`agent_id` во всех методах | `404 not_found`, без `ETag`, без утечки имени |
| T-API-05 | Пагинация (A) | `limit=0/101`, мусорный cursor, вставка во время листания | `422`/`400 invalid_cursor`; нет дублей и пропусков; порядок стабилен |
| T-API-06 | Идемпотентность агента (A) | два POST с одним `Idempotency-Key` | один агент, тот же `201`-ответ, второй ключ не выпущен |
| T-API-07 | `If-Match` (A) | PATCH с устаревшим ETag | `412`, данные не изменены |
| T-API-08 | Claim-исходы (A) | нет кода / истёк / использован / чужой / 2 параллельных | всегда `404 invalid_code` (кроме `409 already_claimed` для привязанного устройства); ровно одна привязка при гонке |
| T-API-09 | Claim-подмена (A) | код создан «чужим устройством», браузер вводит | без `pair_secret` устройство не получает `tenant_id`; кабинет показывает fingerprint |
| T-API-10 | Ingest-auth (A) | `ak_`, `lk_`, device token, отсутствие токена | `403 device_credential_required` / `403` / `202` / `401`; `device_id` в теле игнорируется |
| T-API-11 | Ingest-идемпотентность (A) | один и тот же день отправлен 3 раза | счётчики не удваиваются; outbox пуст |
| T-API-12 | Legacy MCP (A) | golden `tools/list` на старом запросе без `ctx`; узел без `hello` | побайтовое совпадение с 0.4.8-снимком; `proto=1` |
| T-API-13 | Agent-key (A) | `ak_` валидный / отозванный / с чужим `user=` / `lk_` | `200` / `401 agent_revoked` / `404 node_not_found` / агент `default` |
| T-API-14 | Control API isolation (A) | без токена, `Origin: https://evil`, `Host: evil`, не-loopback bind | `401`/`403`/`400`; сокет только `127.0.0.1` |
| T-API-15 | Коды ошибок (A) | по одному тесту на каждый код из реестра | код и `retryable` совпадают, текст ru/en присутствует |
| T-API-16 | Namespace (A) | регистрация узла `account`; GET `/account/login`; шар-URL узла `account` | `400 reserved_username`; кабинет не перехвачен; шар не ломает `/account` |
| T-API-17 | OAuth redirect (M+A) | реальный Google-клиент, prod-домен, `next=//evil`, отмена на экране Google | `303` только на разрешённый путь; отмена → `access_denied`; cookie `__Host-` |

---

## 6. Предложение по владению

| Блок | Владелец (R) | Ревьюер (не владелец) | Комментарий |
| :--- | :--- | :--- | :--- |
| OpenAPI + реестр кодов + contract-тесты | API | A + QA | один источник истины; тесты — из спеки, не из кода |
| OAuth-поток, cookie, CSRF, redirect URI | SEC | API | API проверяет контракт, SEC — модель угроз |
| State machine claim и `pair_secret` | SEC | C + API | нужен второй ревьюер: поток пересекает браузер и устройство |
| Per-agent keys и разрешение токена | BE | API + SEC | одна функция записи ключа |
| Ingest и usage-контракт | BE | API + PERF | лимиты тела/бакетов |
| Локальный control API и namespaces | DESK | API | DESK — клиент, API — контракт |
| `hello`/`welcome`/`ctx` и legacy-совместимость | A | API + QA | Architect фиксирует Ruling |
| Namespace/reserved names/порядок маршрутов | API | QA | регресс-тест обязателен |

**Rulings, которые прошу зафиксировать Planner'у (формат `Ruling: … — … — …`):**
1. `Ruling: pairing — один state machine, /approve и /account/api/claim — два актора одной функции — цена ошибки: два несовместимых потока и дыра в привязке.`
2. `Ruling: namespaces — Wave 1 только /account/api/*, /v1/account/* — Wave 3 — цена: дубли контрактов и 404 в интеграциях.`
3. `Ruling: ошибки — RFC 9457 + MCP isError с code — цена: клиенты не различают отказ и сбой.`
4. `Ruling: hello опционален, отсутствие = proto 1, ctx отсутствует = текущий глобальный режим — цена: сломанная запись у существующих узлов.`
5. `Ruling: ключ агента = ak_ + 43 base64url, параметр agent= не вводить — цена: второй непроверяемый источник identity.`

---

## 7. Чек-лист готовности домена (что проверю лично перед подписанием)

1. [ ] OpenAPI-файл лежит в репозитории; `schemathesis run` зелёный на поднятом стенде (A).
2. [ ] Каждый код из реестра имеет ровно одного владельца и тест, который его производит (A).
3. [ ] `GET /v1/devices/pair/{code}` без `pair_secret` не отдаёт ни токен, ни `tenant_id` (A).
4. [ ] Claim-гонка: два параллельных запроса → один `200`, второй `409/404`; в БД одна привязка (A).
5. [ ] Регистрация узла `account`/`v1` → `400 reserved_username`; `/account/login` не попадает в шар-роут (A).
6. [ ] Golden `tools/list` без `ctx` совпадает с 0.4.8; новый узел без `hello` работает (A).
7. [ ] Отзыв агента A закрывает потоки A и не трогает B (A, на живом туннеле — M).
8. [ ] `redirect_uri` в Google Console побайтово равен вычисленному; расхождение base URL роняет старт (A+M).
9. [ ] Control API: `Origin: https://evil` и `Host: evil` → отказ; порт только loopback (A).
10. [ ] Ingest с `lk_`/`ak_` → `403`; повтор дня не удваивает счётчики (A).
