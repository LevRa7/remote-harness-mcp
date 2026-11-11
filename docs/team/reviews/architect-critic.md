# Architect + Critic (red team) — ревью Wave 1

**Роль:** Architect + Critic / Red Team (AGENT_TEAM §2).
**Дата:** 2026-11-11.
**Область:** `docs/wave-1-auth-account-log-settings.md` (далее W1), `docs/remote_harness_mcp_v2_plan.md` §1.7, 3–5, E1–E5, 7, 9 (далее V2), `docs/team/AGENT_TEAM.md`, `docs/team/WBS.md`, `OWNERSHIP_MATRIX.md`, `TEST_PLAN.md`.
**Код:** `gateway.py`, `core/agent.py`, `core/mcp_tools.py`, `deploy_gateway.sh`, `ops/nginx/*`, `install.sh`, `install.ps1`, `core/paths.py`, `tests/test_gateway_credentials.py`.
**Режим:** только чтение; правился единственный файл — этот отчёт.
**Проверено фактически:** маршруты и состояние `gateway.py`, протокол туннеля, деплой/nginx, установщики, текущие тесты парринга.
**Не проверено:** прогон pytest (в окружении ревью нет pytest — `py -3 -m pytest` → `No module named pytest`), реальный Google OIDC, реальный Windows/macOS, прод-конфиг systemd/nginx на хосте. Там, где вывод требует прогона, стоит «не проверено».

---

## 1. Вердикт

**NOT_READY** (направление верное, начинать реализацию в текущей редакции нельзя).

План правильно выбирает контур управления вместо Edge и правильно изолирует код в `account/`, но в нём есть 6 блокеров: привязка кода отдаёт креденшелы узла любому, кто увидел код; у claim нет канала завершения; `account/` не доставляется и не откатывается деплоем; ingest статистики не имеет аутентификации и tenant-скоупа; не выбрано, где авторитет per-agent ключей; апгрейд переводит существующие link-key в `read_only`, ломая работающие сценарии. Плюс 12 критичных дыр в модели данных, сессиях, отзыве, CORS, rate limit и в «трёх уровнях принуждения», которых в Wave 1 фактически два.

Это не «переписать план»: большинство блокеров закрывается решениями и локальными правками в Sprint 0 (ADR + 4–6 новых строк WBS + 2 теста), после чего план становится READY_WITH_FIXES. Оценки (124 pd против 138/143 в тексте) и календарь при этом придётся перепроверить: 12 pd на QA/security при 111 строках WBS и 8 ручных проверках — занижено.

---

## 2. Findings

Severity: **Blocker** — нельзя начинать/выпускать; **Critical** — дыра в безопасности/данных; **Major** — сломает сценарий; **Minor** — качество/ясность.
Q — вопрос задания, к которому относится находка.

| ID | Severity | Область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| F-01 | **Blocker** | Q4 · claim | Код привязки де-факто становится полным креденшелом узла: после claim любой, кто знает код, забирает `token` и `link_key` неаутентифицированным `GET /v1/devices/pair/{code}`. Сейчас креды выдаются только после admin-approve; W1 заменяет approve на claim из кабинета и **не добавляет подтверждения на устройстве**. | `gateway.py:963-980`; `tests/test_gateway_credentials.py:256-282`; W1:150, 618-630 | Ввести `pair_secret`, который возвращается только узлу при старте; collection требует device token (или pair_secret); для уже зарегистрированных узлов оставить admin-approve как второй фактор; переписать контракт парринга и тесты; T-REV-01. | SEC (A), BE (R), API (C) |
| F-02 | **Blocker** | Q1/Q4 · claim | Завершение claim не доставляется узлу: в туннеле нет heartbeat-фрейма, шлюз разбирает только ответы по `id`, агент не шлёт `hello`; «следующий heartbeat» из Приложения A — это локальный файл, а не канал. | `core/agent.py:665-670, 697-708, 78-81`; `gateway.py:2577-2589`; W1:628-630 | Протокол v2: `hello`/`welcome` + `GET /v1/devices/me` (device token) и локальное состояние claim; старый узел — legacy-ветка без claim-статуса, не ошибка. | A (A), BE (R), API (C) |
| F-03 | **Blocker** | Q1/Q7 · deploy/rollback | `account/` не доставляется и не откатывается: `APP_FILES=(gateway.py)`, бэкап и rollback не знают про `account/`, шаблоны, static и БД; `deploy_gateway.sh` не ставит pip-пакеты; в репозитории нет ни `requirements*.txt`, ни `pyproject.toml`. | `deploy_gateway.sh:91, 107-109, 239-264, 328`; W1:111-114 | OPS-06: полный список файлов/каталогов, `requirements-gateway.txt` + установка на хосте, каталог/владелец `/var/lib/rhm`, backup/restore SQLite, healthcheck, откат на `MESH_ACCOUNT=0` и предыдущий `gateway.py`; тест T-REV-10. | OPS (A), A (C), QA |
| F-04 | **Blocker** | Q6 · usage ingest | Не определена аутентификация узла на `POST /account/api/usage/ingest`: `/account/api/*` — cookie-сессия + CSRF, у узла браузерной сессии нет, device token account-слой не принимает. `usage_daily` не содержит `tenant_id`, поэтому после перепривязки устройства агрегаты прежнего владельца достанутся новому. | W1:215, 367-378, 430-433; WBS `USE-14`; `gateway.py:494-530` | Отдельный router `/v1/nodes/usage` (device token + HMAC/nonce, rate limit), `tenant_id` вычислять на сервере, `usage_daily(tenant_id, device_id, …)`, purge при удалении/перепривязке; T-REV-18. | SEC (A), BE (R), API (C) |
| F-05 | **Blocker** | Q3 · key authority | Не выбрано, где авторитет per-agent ключей: план одновременно требует «в БД только хеш» и `registry[user]["agents"]`, а `verify_token` читает только registry и сравнивает plaintext `link_key`. `save_registry` пишет файл неатомарно (`open(w)` без tmp/replace/lock), claim добавляет второго писателя. | W1:192-194, 402, 508; `gateway.py:305-317, 352-392, 494-530` | Ruling в ADR-006: registry (или `keys.json`) — единственный авторитет для MCP-аутентификации, БД — проекция; атомарная запись (tmp+`os.replace`+lock), сверка, состояния ротации; тесты T-REV-07/08/11. | A (A), BE (R), SEC (C) |
| F-06 | **Blocker** | Q5 · mode/legacy | Апгрейд ломает существующих: legacy `link_key` превращается в агента `default` с дефолтом `read_only`, хотя сегодня этот ключ выполняет `bash_exec`/`write_file`. В Wave 1 нет claims в токене (link key — непрозрачная строка) и нет Edge, поэтому «три уровня принуждения» — фактически два; «read-only нельзя обойти» не доказано для старого узла без `ctx`. | W1:44, 193, 282, 284; `core/mcp_tools.py:232, 309`; `gateway.py:389-392` | ADR: либо legacy-`default` получает `agent` с явным баннером/подтверждением, либо read-only с обязательным чек-листом миграции; в тексте — «2 уровня + будущий JWT», а не «три»; регресс-тест T-REV-23. | A (A), SEC (C), BE (R) |
| F-07 | **Critical** | Q2 · схема | Коллизия имён: `sessions` в W1 — браузерные логины, в V2 `Session` — MCP-сессия агента. Одно имя для двух сущностей делает миграцию Wave 2/3 неоднозначной. | W1:319-328; V2:236, 1451 | Немедленно переименовать в `account_sessions` (или `web_sessions`); имя `mcp_sessions` зарезервировать за V2; обновить 0001 до реализации. | BE (R), A (A) |
| F-08 | **Critical** | Q3 · отзыв | `active_sessions` хранит только `session_id → user`; отзыв «сессий агента» невозможен: `close_sse_streams_for_session` закрывает сессию пользователя, а initialize закрывает последнюю сессию пользователя, не агента. Отзыв одного агента либо закроет всех, либо никого — тест `CAB-08` («другие агенты работают») невыполним без индекса. | `gateway.py:77, 125-139, 219-235, 1404-1418`; W1:196 | Добавить `sessions_by_agent: agent_id → {session_id}`, заполнять при initialize из `auth_ctx.agent_id`; отзыв закрывает только эти сессии; T-REV-09. | BE (R), SEC (C) |
| F-09 | **Critical** | Q2 · удаление | Удаление аккаунта не работает по схеме: у `tenants.owner_account_id` и `devices.tenant_id` нет `ON DELETE`, SQLite по умолчанию не включает FK, `PRAGMA foreign_keys=ON` не заявлен; AUTH-18 обещает каскад, W1 §1 обещает удаление. Итог — либо FK-ошибка, либо молчаливые сироты. | W1:308-350; V2:302 (tombstone); WBS `AUTH-18:32` | `ON DELETE CASCADE` для tenant-owned, `ON DELETE RESTRICT` для audit; `PRAGMA foreign_keys=ON` на каждом соединении; определить «отвязать устройство, но не удалять registry»; T-REV-06. | BE (R), A (C), SEC (C) |
| F-10 | **Critical** | Q1 · middleware | Глобальный `CORSMiddleware(allow_origins=["*"])` оборачивает и `/account`: все ответы кабинета и его API получат `Access-Control-Allow-Origin: *`, а preflight `OPTIONS` короткозамыкается до CSRF/Origin-проверок. CSP/XFO/Referrer-Policy для кабинета не заявлены нигде, кроме слова «CSP» в таблице угроз. | `gateway.py:3382-3390`; W1:506 | Не монтировать account под глобальный CORS: обернуть приложение middleware, который не добавляет ACAO для `/account`, либо вынести CORS в MCP-маршруты; на account-ответах — CSP, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`; контрактный тест T-REV-12. | SEC (A), BE (R), UI (C) |
| F-11 | **Critical** | Q1/Q6 · rate limit | `client_ip()` берёт **первый** `X-Forwarded-For`, а nginx **дописывает** реальный IP в конец: клиент подставляет `X-Forwarded-For: 1.2.3.4` и обходит лимиты register/pair, а если claim сделают через `rate_limited("claim", client_ip(...))` — и лимит перебора кода. | `gateway.py:726-730`; `ops/nginx/remote-harness-mcp.conf:150` | Брать `X-Real-IP` (nginx его перезаписывает) или последний элемент XFF; для claim — лимит по аккаунту **и** по IP, счётчик в БД, TTL-окно; тест T-REV-13. | SEC (A), BE (R) |
| F-12 | **Critical** | Q2 · модель | Модель Wave 1 не является объявленным «фундаментом» и создаёт тупик: нет `tenant_members` (org, V2 Q9), `tenants.plan/bridge_id/data_region`, `agents.oauth_client_id`, `grants`, `devices.pubkey/cert_fpr`, `audit_events.prev_hash/hash`, `usage_events`. V2 Appendix B требует всё это; добавление после реализации — backfill и двойная миграция. | W1:305-398; V2:228-240, 299-302, 1443-1459 | В 0001 добавить nullable-колонки и `tenant_members` (даже с одной строкой), `grants` создать пустой; зафиксировать «Wave 1 — подмножество V2 Appendix B» в ADR; тест diff-схемы. | A (A), BE (R) |
| F-13 | **Critical** | Q6 · статистика | Обещанная точность недостижима: `usage_daily` — только суммы, p50/p95 из неё не считаются (V2 E4 и WBS `USE-08` их обещают); отказы, сделанные мостом, до узла не доходят, поэтому «кабинет == локальная сводка ±1%» ложно по построению; при обрыве туннеля узел может записать `ok`, а клиент получить ошибку. | W1:230, 367-378, 411-433; V2:637; `gateway.py:2310-2319`; `core/agent.py:618-622` | Зафиксировать инвариант «узел — единственный источник истины», добавить `lost`/`unknown` в `status`, `call_id`, histogram-бакеты (или raw events с ретенцией) для p95, отдельный счётчик `denied_at_bridge`; заменить «±1%» на контракт сверки; T-REV-14/15. | BE (R), QA (C), PERF (C) |
| F-14 | **Critical** | Q2/Q7 · идентичность | `accounts.google_sub TEXT UNIQUE` без `iss`/`aud`: Google `sub` уникален в рамках клиента. V2 E11 допускает вендорный брокер (A) и собственный клиент self-host (B) — один человек получит два аккаунта, а миграция устройств между ними не описана. | W1:309; V2:1104-1106 | Хранить `google_iss`, `aud` (`client_id`), UNIQUE(`iss`,`sub`), email как вторичный ключ; продумать merge-сценарий до Wave 3. | SEC (A), BE (R), A (C) |
| F-15 | **Critical** | Q5 · SessionContext | Механизма передачи контекста нет: обработчики `mcp_tools` принимают только `args`, `_WORKSPACE`/`_READ_ONLY` — глобальные переменные; вызов идёт через `asyncio.to_thread` (contextvars копируются только для этого вызова), а джобы — отдельные `threading.Thread`, куда контекст не наследуется; `configure()` вызывается один раз при импорте. Итог: per-agent mode/workspace либо не доедет до джобов, либо протечёт между агентами. | `core/mcp_tools.py:232, 289-311, 403-412, 2597-2598, 2642, 3485-3500`; `core/agent.py:598`; W1:280-284 | Явный `SessionContext`-параметр на каждый handler **или** contextvars с `copy_context()` в job-потоки; `workspace` проверять в момент вызова, а не при старте; тесты на параллельные вызовы двух агентов и на джоб, переживший смену режима (T-REV-21/22). | BE (R), A (C), SEC (C) |
| F-16 | **Critical** | Q4 · контракт | Переиспользование `POST /v1/devices/pair` под новую семантику ломает существующий контракт и тесты: сейчас эндпоинт не принимает токен и ничего не создаёт, approve требует `MESH_ADMIN_TOKEN`; W1 делает code-центричный claim. Плюс `account` отсутствует в `RESERVED_NAMES`, поэтому узел с именем `account` потеряет свои share-URL под `Mount("/account")`, а catch-all `/{user}/{rest:path}` стоит последним. | `gateway.py:296-303, 919-1030, 3379`; `tests/test_gateway_credentials.py:226-282` | Новые версионированные маршруты `/v1/devices/claim/start|status` (pair оставить как есть), `account`/`pair` в `RESERVED_NAMES`, `Mount` строго до catch-all, тест на маршрутизацию. | API (A), BE (R), QA |
| F-17 | **Critical** | Q4/Q7 · порядок | Claim в Sprint 1 требует, чтобы устройство показало код, но носителя нет: трей — Sprint 3, в `install.sh`/`install.ps1` парринг не используется вообще (только `/api/register`). CLA-10 «статус для трея» тоже не имеет потребителя. | W1:150, 243-270, 618-630; `install.sh:1901, 1915`; `install.ps1:973`; WBS `CLM-10` | Добавить `CLM-11` (CLI `rhm pair` + запись кода в `agent.env`/консоль, headless-сценарий) владельцем BE/DESK в Sprint 1; трей подхватывает позже; тест T-REV-05. | P (A), BE (R), DESK (C) |
| F-18 | **Critical** | Q6 · изоляция | `usage_daily` не имеет `tenant_id`/`account_id` и FK; после `AUTH-18` (устройство отвязано/отозвано) и повторного claim другим аккаунтом строки прошлого владельца либо остаются навсегда, либо (если device_id переиспользован) показываются новому. | W1:367-378, 402; WBS `CLM-08`, `AUTH-18` | `usage_daily(tenant_id, device_id, agent_id, day, tool)`; при смене владельца — новый `device_id` **или** purge старых строк; тест T-REV-18. | BE (R), SEC (C) |
| F-19 | **Major** | Q1 · nginx | Regex-локация `\.(…|json)$ → 404` отдаёт 404 на любой `.json`: i18n-строки "в JSON" (W1:490) и любые `.json`-эндпоинты кабинета не доедут; отдельной локации/лимитов для `/account` нет, `/account` попадает в общий `location /`. | `ops/nginx/remote-harness-mcp.conf:5-12, 144-153`; `ops/nginx/mesh-domain.vhost.template:46-57`; W1:490 | Вынести i18n в `.js`/Jinja или исключить `/account/` из regex; добавить `location /account` с `proxy_buffering off`, `client_max_body_size`, `limit_req`; тест T-REV-19. | OPS (A), UI (C), QA |
| F-20 | **Major** | Q1 · блокировки | Синхронный SQLite в общем event loop с SSE/туннелем: любой запрос кабинета (или 100 МБ scan для summary) блокирует `call_remote_tool`, heartbeat и watchdog; WAL/busy_timeout/однописатель не заявлены, `load_registry()` читает файл на каждый MCP-вызов. | `gateway.py:551-614, 305-317`; W1:400, 426 | `run_in_threadpool` для всех DB-операций, WAL + `busy_timeout`, одна writer-очередь/лок, кэш registry с инвалидацией по mtime; budget p95 в тесте нагрузки `OPS-08`. | PERF (A), BE (R), A (C) |
| F-21 | **Major** | Q6 · путь журнала | W1 предлагает `/etc/...` «для службы», но установщик ставит узел как `systemctl --user` (и Startup текущего пользователя на Windows), а `core/paths.config_dir()` — per-user `~/.config`. Два пути = трей не видит журнал службы, либо файл пишется root и недоступен. | W1:210; `core/paths.py:40-49`; `install.sh:2007-2026`; `install.ps1:1051-1070` | Единственный путь через `core/paths.config_dir()` + `MESH_STATE_DIR` для служб; ACL/владелец; тест T-REV-24. | BE (R), DESK (C) |
| F-22 | **Major** | Q6 · ротация | Ротация «10 МБ × 5» на Windows не может переименовать открытый файл; очистка из трея/CLI конкурирует с append из агента; в плане нет lock/порядка close→rename→reopen, нет политики при сбое питания во время ротации. | W1:414-433; WBS `USE-04/10` | Отдельный writer с mutex + закрытие перед rename; `O_APPEND` на POSIX; на Windows — `msvcrt.locking`/lock-файл; clear создаёт новый файл и чистит outbox; fault-injection тест. | BE (R), QA (C) |
| F-23 | **Major** | Q6 · CSV/labels | `agent_label` — управляемая клиентом строка (`clientInfo.name`), попадает в CSV и UI; CSV-инъекция (`=`, `+`, `-`, `@`) и XSS не закрыты в плане; «тест-сканер на секреты» проверяет только payload, не формулы. | W1:213-216, 429-433; WBS `CAB-04`, `USE-09` | Санитизация CSV (префикс `'`), allowlist полей журнала, label ≤64 и без управления; property-based тест (hypothesis) на отсутствие подстрок аргументов; T-REV-17. | BE (R), SEC (C), QA (C) |
| F-24 | **Major** | Q1/Q7 · API | Контракт кабинета расходится с V2: W1 — `/account/api/*` (внутренний, без версии), V2 E4 — `/v1/account/*`; коды ошибок claim (400 vs 404 vs 409) и анти-энумерация не согласованы; в WBS нет ни одной строки API-контракта, хотя RACI отдаёт контракт роли API. | W1:157-163, 152; V2:653-667; `AGENT_TEAM.md:50`; `WBS.md` | Выбрать `/v1/account` как публичный контракт (W1 монтирует его же), OpenAPI + contract-тесты; единая таблица кодов; новый блок `API-01` владельцем API. | API (A), BE (R), P (C) |
| F-25 | **Major** | Q1/Q7 · процесс | ADR-004/005 — работы Sprint 0, но в WBS их нет: ни владельца, ни теста, ни доказательства. Матрица противоречит RACI: OIDC/сессии R/A = SEC, а владелец AUTH-01…18 — BE; у Architect нет ни одной строки. | W1:125-133; `AGENT_TEAM.md:50-52`; `OWNERSHIP_MATRIX.md:8-25`; `WBS.md` | Добавить `ARC-01` (ADR-004), `ARC-02` (ADR-005/006), `API-01`; в матрице owner AUTH/CLM = SEC, BE — исполнитель; reviewer A/C; правило «ADR без WBS не существует». | P (A), A (R), QA (C) |
| F-26 | **Major** | Q7 · план/оценки | Оценки противоречат друг другу (120 / 124 / 138 / 143 pd), Sprint 0 «3–4 дня» при 8 pd и 4,5 FTE, QA/security 12 pd на 111 строк WBS, 8 ручных проверок и security-чеклист; базовая линия «434+ тестов» устарела: в `tests/` статически 675 `def test_`, фактический прогон не проверен. | W1:5, 121-136, 560-569; V2:84, 120, 1250 | Пересчитать по строкам WBS, выделить QA ≥ 25 pd и отдельный security-бюджет; заменить «434» на измеренное число из CI; зафиксировать в TEST_PLAN. | P (A), QA (R) |
| F-27 | **Major** | Q1 · OIDC | Где и как хранятся `state`/`nonce`/PKCE-verifier (cookie? БД? TTL? одноразовость?) — не сказано; `__Host-` требует `Secure`, что ломает dev-режим на `http://localhost`; `redirect_uri` должен точно совпадать с `MESH_ACCOUNT_BASE_URL`, но проверка Host/base не описана. | W1:144-148, 513; WBS `AUTH-02/07` | Явно: login-сессия в БД/подписанной cookie с TTL 10 мин, одноразовый state, привязка к pre-login cookie; dev-режим — отдельное имя cookie без `__Host-`; старт-проверка `base_url`/`redirect_uri`. | SEC (A), BE (R) |
| F-28 | **Major** | Q1/Q7 · границы | `account/` с Google client secret и cookie-сессиями ставится в тот же `gateway.py`, который `deploy_gateway.sh` публикует любому self-host. Это противоречит V2 §3.2 (self-host оператор недоверенный) и E11 (аккаунт на edge): на self-host кабинета быть не должно, иначе оператор моста получает вход в аккаунты. | V2:190-195, 1104-1112; `deploy_gateway.sh:91` | Жёсткий `MESH_DEPLOYMENT=vendor|selfhost`: при `selfhost` account-слой не монтируется и не читает Google-секреты; ADR-005 фиксирует «account живёт только на вендорском хосте до Edge». | A (A), SEC (C), OPS |
| F-29 | **Major** | Q5 · отзыв/revoke | «Отзыв устройства» в кабинете не определён: `device_revoke` удаляет запись registry и рвёт туннель (необратимо, повторная привязка — только новым кодом), а W1 §8 говорит про «опасную зону» и «данные помечаются». Для офлайн-узла это означает тихую потерю доступа. | `gateway.py:1131-1155`; W1:202, 487; WBS `CAB-05` | Разделить «отвязать от аккаунта» (registry остаётся, туннель жив, device token жив) и «отозвать узел» (delete + причина + подтверждение имени); аудит; тест. | BE (R), UI (C), SEC (C) |
| F-30 | **Major** | Q5 · трей | «Выход» останавливает трей, «узел продолжает службой» — но на Windows узел ставится как Startup-процесс/задача текущего пользователя, на Linux — `systemctl --user`; control-token 0600 у пользователя и трея один, а «служба» и трей могут оказаться в разных сессиях. Автозапуск в меню относится к трею, а не к узлу. | W1:267-270, 455; `install.ps1:1051-1070`; `install.sh:2007-2026` | Зафиксировать модель: один пользователь = один агент + трей; автозапуск регистрирует и узел, и трей (или только узел, трей стартует по требованию); тест перезагрузки (ручной M-02). | DESK (A), OPS (R), UI (C) |
| F-31 | **Major** | Q5 · tools/list | Фильтрация `tools/list` по scopes ломает parity-тест: список инструментов в `gateway.py` статичен и обязан совпадать с `core/mcp_tools.TOOLS`; при этом мутирующие инструменты помечены `readOnlyHint: true`, поэтому клиент не показывает диалог — вся защита обязана быть на мосте/узле, а не в UI-подсказке. | `gateway.py:1473-1477, 3031-3104`; `tests/test_tool_annotations.py`; W1:281 | Фильтровать до `annotate_tools`, обновить parity-тест (сравнивать полный набор, а не выдачу агенту); тест «read_only видит N, agent M, старый клиент не ломается». | BE (R), SEC (C), QA |
| F-32 | **Major** | Q5 · гонка вызова | `call_remote_tool` после обрыва туннеля повторяет тот же `tools/call` на новом соединении: мутирующая команда может выполниться дважды, а журнал/статистика увидят одну или две записи в зависимости от версии узла. | `gateway.py:592-614`; `core/agent.py:605-622` | Ввести `call_id` (idempotency key) в фрейм, дедуп на узле (LRU 5 мин), один журнальный record на `call_id`, retry с тем же id; тест T-REV-16. | BE (R), A (C) |
| F-33 | **Minor** | Q7 · нейминг | Три набора флагов на одну фичу: `MESH_ACCOUNT` (W1), `RHMV2_ACCOUNTS` (V2), `MESH_AUTH_MODE`/`MESH_GOOGLE_*`; runbook и тесты будут расходиться. | W1:111-115, 632-644; V2:220 | Один префикс (`MESH_ACCOUNT_*`) и алиасы только на чтение; таблица «флаг → фаза» в ADR. | A (R), OPS (C) |
| F-34 | **Minor** | Q1 · хуки | W1 обещает «Mount и 3–4 хука», по факту нужно ≥7 (auth-ctx, pair/claim, registry write, initialize/agent upsert, enforce call, close sessions, online status, usage ingest). Иначе `account/` начнёт импортировать `gateway` и появится цикл. | W1:91; `gateway.py:494-530, 551-614, 1385-1459, 2533-2603` | Зафиксировать `BridgeHooks` (Protocol) и список методов в ADR; оценку хуков включить в Sprint 0. | A (A), BE (R) |
| F-35 | **Minor** | Q6 · формат | «Нет аргументов/путей» — верно, но `agent_label`, точный `ts` и `bytes` остаются метаданными; `bytes` на узле и на мосте расходятся из-за обрезки 45k; `lost` в enum отсутствует. | W1:411-433; `gateway.py:2310-2319` | Enum статусов + `lost`, `bytes` — только с узла, label санитизировать, ts округлять при экспорте, описать в `/account/privacy`. | BE (R), SEC (C) |
| F-36 | **Minor** | Q7 · OAuth-заглушка | `/.well-known/oauth-protected-resource` явно рекламирует `bearer_methods_supported: ["query","header"]`, что противоречит политике «device token только в header»; в Wave 2 это придётся менять, а клиенты закэшируют. | `gateway.py:1158-1183` | До Wave 2 оставить `authorization_servers: []` и `bearer_methods_supported: ["header"]`; задокументировать изменение. | API (R), SEC (C) |
| F-37 | **Minor** | Q1 · мелочи | `is_link_key()` не используется (мёртвый код); `active_sessions`/`latest_session_by_user` не чистятся по TTL (рост памяти); у роли DOC нет ни одной строки WBS, хотя DoD требует README/ACCOUNT/privacy. | `gateway.py:374, 77-78, 232-234`; `AGENT_TEAM.md:38`; W1:550 | Удалить мёртвый код; TTL-чистка сессий в watchdog; `DOC-01` (README/ACCOUNT/privacy/changelog) с владельцем DOC. | BE (R), DOC (C) |

---

## 3. Пропущенные детали

Всё, что не описано в W1/V2, но обязательно для работоспособности. ID — предлагаемые строки WBS (по правилу `WBS.md:174` — с префиксом `REV-`, существующие строки не удалять).

| ID | Деталь | Почему без неё нельзя | Владелец |
| :--- | :--- | :--- | :--- |
| REV-ARC-01 | ADR-004: имя `account_sessions`, место `account/`, cookie/login-сессия, `state/nonce` хранилище, single-process допущение | Иначе 0001 фиксирует коллизию имён и неясный OIDC-контур (F-07, F-27) | A |
| REV-ARC-02 | ADR-005/006: авторитет ключей (registry vs DB), направление синхронизации, словарь scopes, legacy-режим, «2 уровня + JWT» | Без решения нельзя писать `verify_token` и `AGT-*` (F-05, F-06, F-12) | A |
| REV-CLM-11 | Носитель кода: `rhm pair` CLI + запись в `agent.env`/консоль; headless-сервер; трей — позже | Claim в Sprint 1 иначе нечем принять (F-17) | BE/DESK |
| REV-CLM-12 | Канал завершения claim: `hello`/`welcome`, `GET /v1/devices/me`, локальное состояние `unbound/pending/bound` | Без него устройство никогда не узнает `device_id` (F-02) | A/BE |
| REV-CLM-13 | `pair_secret` и модель сбора кредов (кто и с каким секретом забирает device token) | Устраняет кражу кода (F-01) | SEC/BE |
| REV-CLM-14 | Поведение при офлайне/повторе/чужом аккаунте/уже привязанном/отозванном узле/ротации токена между стартом и claim | Гонки и состояния не описаны (Q4) | BE |
| REV-AGT-11 | Индекс `session_id → agent_id` и отзыв только сессий агента | Тест CAB-08 невыполним (F-08) | BE |
| REV-AGT-12 | Состояния ключа: `active → rotating → revoked`, grace-период, дедуп `key_sha256`, `INSERT … ON CONFLICT`, cap агентов | Гонки initialize/rotation/revoke (Q3) | BE |
| REV-AGT-13 | Кросс-проверка `agent.device_id.node_name == ?user=` при резолве ключа | Ключ узла A нельзя предъявить как узел B | SEC |
| REV-CAB-15 | Публичный префикс API (`/v1/account`) + OpenAPI + таблица кодов ошибок + анти-энумерация | Иначе Wave 2/3 сломают контракт (F-24) | API |
| REV-CAB-16 | Семантика «отвязать» vs «отозвать» устройство, что происходит с ключами/туннелем/данными | `device_revoke` необратим (F-29) | BE/UI |
| REV-USE-15 | `call_id` как ключ идемпотентности журнала и retry | Двойное выполнение/двойной учёт (F-32) | BE |
| REV-USE-16 | `tenant_id` в `usage_daily`, purge при удалении/перепривязке, ретенция per tenant | Утечка статистики новому владельцу (F-04, F-18) | BE/SEC |
| REV-USE-17 | Санитизация CSV/label, allowlist полей, property-based тест на секреты | CSV-инъекция/XSS (F-23) | BE/QA |
| REV-USE-18 | Lock/ротация/очистка на Windows, один writer, путь через `core/paths` | Порча файла и невидимый трею журнал (F-21, F-22) | BE |
| REV-MOD-09 | Механизм передачи контекста: `call_tool(name, args, ctx)` или contextvars + `copy_context()` в job-потоки | Без него per-agent mode/workspace не работает (F-15) | BE |
| REV-MOD-10 | Политика legacy-`default` (agent vs read_only) и баннер/подтверждение при апгрейде | Иначе регресс существующих (F-06) | A/SEC |
| REV-SEC-09 | Threat model claim/pair (кража кода, login CSRF, чужой аккаунт), включая `pair_secret` | Без модели не написать тесты (F-01, F-27) | SEC |
| REV-SEC-10 | Заголовки кабинета (CSP, XFO, Referrer-Policy, no-store) и обход глобального CORS | CORS `*` на аутентифицированных ответах (F-10) | SEC |
| REV-OPS-09 | Доставка `account/` (файлы/каталоги/шаблоны/static), владелец `/var/lib/rhm`, backup/restore SQLite, down-миграции | Иначе релиз и откат невозможны (F-03) | OPS |
| REV-OPS-10 | nginx: `location /account`, исключение `.json`, `limit_req` для claim/ingest | 404 на i18n и отсутствие лимитов (F-11, F-19) | OPS |
| REV-OPS-11 | `requirements-gateway.txt`, установка на хосте, guarded import `account` (ImportError → кабинет 503, MCP жив) | Отсутствие зависимостей убьёт шлюз (F-03) | OPS |
| REV-OPS-12 | Атомарная запись registry (tmp+replace+lock) и сверка registry↔DB | Потеря регистраций при сбое (F-05) | BE/OPS |
| REV-OPS-13 | Явное допущение «один процесс» для `pending_pairs`/rate limit/сессий | При `--workers > 1` claim/парринг ломаются | OPS |
| REV-PERF-01 | SQLite вне event loop + WAL + busy_timeout | Блокировка туннеля/SSE (F-20) | PERF |
| REV-PERF-02 | Кэш registry/OIDC JWKS с инвалидацией | Лишний диск на каждый вызов | PERF |
| REV-QA-01 | Зафиксировать измеренную базовую линию тестов и время прогона | «434+» устарело (F-26) | QA |
| REV-DOC-01 | README/ACCOUNT/privacy/changelog + страница «что видит сервер» | DoD требует, WBS не содержит (F-37) | DOC |

---

## 4. Пропущенные тесты

Новые кейсы, которых нет в `TEST_PLAN.md`. Формат: шаги → ожидание; «Авто» = можно в pytest/CI, «Ручн» = только стенд/железо.

| ID | Сценарий | Ожидание | Авто/Ручн | WBS |
| :--- | :--- | :--- | :--- | :--- |
| T-REV-01 | Узел стартует claim и получает код; «атакующий» с кодом опрашивает статус до узла | Ни device token, ни link key не выдаются; только `pair_secret`-держатель получает креды; событие `claim_failed` | Авто | CLM-01/13 |
| T-REV-02 | Два браузера (разные аккаунты) одновременно claim одного кода; третий — без сессии | Ровно один 200 у владельца кода, остальные 409/404 без деталей; `devices`/`registry` согласованы; ровно одна запись в аудите | Авто (2 потока) | CLM-03/05/08 |
| T-REV-03 | Claim при офлайн-узле: код выдан, узел отключился, браузер подтвердил, узел вернулся | `GET /v1/devices/me` отдаёт `bound` + `device_id`; дубля устройства нет; журнал/аудит полны | Авто + Ручн | CLM-10/12 |
| T-REV-04 | Claim кода узла, который отозван/удалён между стартом и claim | 404/409 без деталей; registry не изменён; tunnel не создан | Авто | CLM-09 |
| T-REV-05 | Легаси-узел 0.4.x: claim по коду, старый `link_key` продолжает работать | Новый device token не выдан; URL работает; `device_id/tenant_id` появились в registry | Авто (fixture registry) | CLM-07 |
| T-REV-06 | Удаление аккаунта с tenant/device/agent/usage | Сессии/агенты удалены, устройство отвязано (или удалено) по ADR; registry-запись отвязана, туннель жив; audit-tombstone; ошибок FK нет | Авто | AUTH-18 |
| T-REV-07 | Ключ агента узла A предъявлен как `?user=B`; ключ отозванного агента; ключ после истечения grace | 401 во всех трёх; `agent_id` не резолвится в чужой узел | Авто | AGT-06/13 |
| T-REV-08 | Ротация ключа во время `initialize`; два параллельных initialize с одним ключом | Нет дубля агента; `last_used` обновлён; старый ключ недействителен по политике; in-flight не падает | Авто | AGT-08/12 |
| T-REV-09 | Два агента на устройстве; отзыв одного при живых SSE-стримах | Закрыты только сессии отозванного; второй агент получает результат; in-flight по политике; 401 на следующий вызов | Авто + Ручн (M-03) | CAB-08/AGT-11 |
| T-REV-10 | `MESH_ACCOUNT=1`, но `jinja2`/`PyJWT` отсутствуют (subprocess с заблокированным import) | `/health` 200, `initialize`/`tools/call` работают, `/account` — понятная 503-страница; в логах нет секретов; шлюз не падает | Авто | OPS-03/11 |
| T-REV-11 | `kill -9`/исключение в момент записи registry и в момент claim-транзакции | registry — валидный JSON или остался `.tmp`; `verify_token` работает; повторный claim идемпотентен или 409 | Авто (fault injection) | CLM-06/OPS-12 |
| T-REV-12 | `OPTIONS /account/api/claim` и GET страницы с `Origin: https://evil` | Нет `ACAO: *` на account-ответах; CSP/XFO/Referrer-Policy/no-store присутствуют; cookie `__Host-` c нужными флагами | Авто (contract) | SEC-04/SEC-10 |
| T-REV-13 | 100 попыток claim с меняющимся `X-Forwarded-For` и одним аккаунтом | Лимит по аккаунту срабатывает; 429 с `Retry-After`; валидные заявки не страдают | Авто | SEC-07 |
| T-REV-14 | 100 вызовов, из них 10 отклонены мостом по scope, 2 оборваны на середине | Узел пишет 88 `ok/error`, мост — 10 `denied_at_bridge`, 2 `lost`; кабинет и локальная сводка сходятся по зафиксированному инварианту, а не «±1%» | Авто | USE-07/14 |
| T-REV-15 | Попытка посчитать p50/p95 из `usage_daily` | Тест фиксирует ограничение: либо histogram-бакеты, либо отказ от p95 в кабинете | Авто | USE-08/CAB-10 |
| T-REV-16 | Обрыв туннеля после отправки мутирующего вызова; retry шлюза | Команда выполнена один раз; один journal-record на `call_id`; клиент получает ровно один результат | Авто + Ручн | MOD-06 |
| T-REV-17 | `agent_label = "=cmd|' /C calc'!A0"`; метка с `<script>`; экспорт CSV | CSV-ячейка экранирована, XSS не исполняется, UI не ломается | Авто | CAB-04/USE-09 |
| T-REV-18 | Устройство привязано к A, отвязано, привязано к B | Кабинет B не видит ни одной строки usage A; outbox A не отправляется; purge/новый device_id | Авто | USE-16 |
| T-REV-19 | Рендер vhost: `GET /account/static/i18n/ru.json` и `/account/api/session` | 200 (или i18n в `.js`), `/account` не попадает под `.json → 404`; `limit_req` на claim/ingest | Авто (nginx -t + curl) | OPS-10 |
| T-REV-20 | `tools/list` для read_only и agent; старый клиент без `ctx` | Наборы различаются; parity-тест полного набора зелёный; старый узел получает дефолт, а не ошибку | Авто | MOD-03/06/09 |
| T-REV-21 | Два агента параллельно с разными workspace; джоб, запущенный до смены режима | Пути не пересекаются; джоб доигрывает по политике; новый вызов — в новом workspace/режиме | Авто | MOD-01/07/08 |
| T-REV-22 | `MESH_READ_ONLY=1` + `ctx.mode=agent` + scopes на shell | Отказ `mode_denied`; глобальный предохранитель старше ctx; журнал фиксирует отказ | Авто | MOD-02/04 |
| T-REV-23 | Апгрейд registry 0.4.8: legacy `link_key` + `bash_exec` | Поведение по ADR (agent с баннером или read-only с инструкцией); старый URL работает; регресс-тест в OPS-04 | Авто | AGT-07/OPS-04 |
| T-REV-24 | Журнал пишется узлом под `systemctl --user`, читается треем; файл 0600/ACL | Один и тот же файл, трей читает, посторонний пользователь — нет; после рестарта путь тот же | Ручн (Win/Linux) | USE-03/18 |
| T-REV-25 | Outbox: 2 дня офлайна, затем reconnect, повторная отправка после сбоя | Суммы точные, дублей нет, размер ограничен, старые ревизии не перетирают новые | Авто | USE-13/14 |

---

## 5. Предложение по владению

Существующие 111 строк WBS сохраняются; ниже — блоки, владельцы и обязательный второй ревьюер. Правило «владелец ≠ ревьюер» соблюдено.

| Блок | Owner | Reviewer | Второй ревьюер / условие | Комментарий |
| :--- | :--- | :--- | :--- | :--- |
| AUTH-01…18 | **SEC** | A | C — на threat model login/CSRF | RACI требует SEC владельцем OIDC; BE — исполнитель, не accountable |
| CLM-01…14 | **SEC** | A | QA — на гонки claim | Самый опасный поток; F-01/F-02/F-17 обязательны до G3 |
| CAB-01…16 | **UI** | API | SEC — на опасные действия (revoke/delete) | API-контракт (`API-01`) утверждает API, не UI |
| AGT-01…13 | **BE** | SEC | QA — на миграцию/ротацию | F-05/F-08/F-12 закрыть в Sprint 2 |
| USE-01…18 | **BE** | QA | SEC — на приватность/CSV/ingest | Инварианты журнала — в TEST_PLAN, не в коде |
| CTL-01…10 | **BE** | DESK | SEC — на localhost/token | Control API — граница доверия трея |
| TRY-01…11 | **DESK** | UI | OPS — на автозапуск/перезагрузку | Ручные M-02/M-06 обязательны |
| MOD-01…10 | **BE** | SEC | PERF — на ctx в потоках/джобах | Рефактор `_READ_ONLY` — риск регресса 675 тестов |
| SEC-01…10 | **SEC** | C | A — на архитектурные границы | SEC не может быть единственным ревьюером самой себя |
| OPS-01…13 | **OPS** | QA | C — на откат/деплой | F-03/F-10/F-19 блокируют G5 |
| ARC-01…02, API-01 (новые) | **A** / **API** | P | C — на ADR-006 | Без них Sprint 0 не имеет выхода |
| PERF-01…02 (новые) | **PERF** | BE | — | DB/registry в горячем пути |
| DOC-01 (новый) | **DOC** | UI | — | README/ACCOUNT/privacy/changelog |

**Где нужен второй ревьюер обязательно:** CLM (безопасность + гонки), MOD (безопасность + производительность), USE (приватность + QA), OPS (откат + QA). Для остальных достаточно одного независимого ревьюера по RACI.

**Что менять в матрице сейчас:** owner AUTH/CLM → SEC; добавить ARC-01/02, API-01, PERF-01/02, DOC-01; reviewer для SEC-* — C или A, но не SEC.

---

## 6. Чек-лист готовности домена (что я проверю лично перед подписью)

- [ ] **G0/ADR:** ADR-004/005/006 подписаны; в них есть: имя `account_sessions`, авторитет ключей, направление синхронизации registry↔DB, словарь scopes, политика legacy-`default`, «2 уровня + JWT».
- [ ] **Claim threat model:** SEC предъявил модель кражи кода/login CSRF; `pair_secret` и collection-контракт реализованы; тесты T-REV-01/02/03/04 зелёные (свежий вывод).
- [ ] **Схема 0001:** diff против V2 Appendix B снят; `tenant_members`, nullable `plan/bridge_id/oauth_client_id/pubkey/prev_hash/hash`, `usage_daily.tenant_id`; down-миграция прогнана на копии; `PRAGMA foreign_keys=ON` проверен.
- [ ] **Деплой/откат:** `deploy_gateway.sh` ставит `account/` и зависимости, создаёт `/var/lib/rhm` с нужным владельцем, делает backup SQLite; откат на предыдущий `gateway.py` + `MESH_ACCOUNT=0` проверен на стенде; healthcheck после отката.
- [ ] **Guarded import:** при отсутствии `jinja2`/`PyJWT` шлюз стартует, `/health` 200, MCP `initialize`/`tools/call` работают, `/account` — 503; тест T-REV-10.
- [ ] **Node-side:** claim-код показывает реальный узел (CLI/трей), `GET /v1/devices/me` возвращает `bound`, старый узел 0.4.x подключается и работает (T-REV-03/05/23).
- [ ] **Mode:** матрица «инструмент × mode × слой» зелёная, включая прямой вызов узла, старый узел без ctx, параллельные агенты, джоб после смены режима, приоритет `MESH_READ_ONLY` (T-REV-20/21/22).
- [ ] **Безопасность кабинета:** на `/account` нет `ACAO: *`, есть CSP/XFO/no-store; CSRF/Origin тесты; XFF-спуфинг не обходит лимиты; cookie-флаги проверены в браузере (T-REV-12/13).
- [ ] **Журнал:** один record на `call_id`, нет payload, `lost/denied` учтены, ротация/очистка на Windows, путь через `core/paths`, CSV-инъекция закрыта (T-REV-14/16/17/24/25).
- [ ] **Регресс:** полный набор тестов в CI зелёный; parity-тесты `tools/list` обновлены; число тестов и время прогона зафиксированы (а не «434+»).

---

## Приложение A. Ответы на 8 вопросов

### A1. «Кабинет в `gateway.py` по `/account`» — выдерживает ли проверку границ и отката?

**Частично.** Решение «код в `account/`, в шлюзе только mount» верное, но в текущей редакции границы не выдержаны: в шлюзе нет ни одного шва для account, кроме импорта, поэтому появятся либо циклический импорт (`account` → `gateway`), либо копирование логики (`verify_token`, `pending_pairs`, `active_tunnels`, registry). Плюс: mount наследует глобальный CORS (F-10), попадает под nginx-правила (F-19), не доставляется деплоем (F-03), а `account` не зарезервирован под имена узлов (F-16).

**Циклические зависимости:** `gateway` импортирует `account.app` лениво (внутри `build_routes()`), `account` не импортирует `gateway` вообще; всё общение — через `BridgeHooks` (Protocol), который шлюз собирает из своих функций. Если `account` начинает `from gateway import load_registry` — цикл и невозможность тестировать пакет отдельно.

**Конфликты middleware:** единственный middleware сейчас — `CORSMiddleware(allow_origins=["*"])`; он оборачивает всё приложение, включая смонтированное sub-app. Нужен свой ASGI-слой, который для `/account` не добавляет ACAO и добавляет CSP/XFO/no-store; внутри account — CSRF/Origin и (опционально) SessionMiddleware. Нельзя ставить глобальный `SessionMiddleware` — он подпишет и MCP-ответы.

**Блокировки:** `sqlite3` синхронный; обработчики Starlette асинхронные и делят loop с туннелем/SSE. Любой запрос БД без `run_in_threadpool` останавливает вызовы инструментов и heartbeat. Нужны WAL, `busy_timeout`, один writer, вынос в threadpool (F-20). Второй источник блокировок — `load_registry()` на каждый MCP-вызов и неатомарная запись (F-05).

**Рост сложности:** план недооценивает шов: не «3–4 хука», а ≥7 (F-34). Каждый хук — новая точка контракта и тестов. Это приемлемо, но должно быть в WBS и оценке.

**Минимальная структура `account/` и точки интеграции** — Приложение B.

### A2. Достаточно ли модели `Account/Tenant/Device/AgentConnection`? Тупик для Wave 2/3?

Для Wave 1 — **почти достаточно**, для Wave 2/3 — **тупик, если не добавить nullable-поля сейчас** (F-12). Чего не хватает:

- `tenant_members` (личный тенант = одна строка; org — Wave 2.1/Q9) — иначе «пригласить в тенант» потребует переделки всех FK.
- `tenants.plan`, `bridge_id`, `data_region` (V2 §4.1) — Wave 3 миграция без них не начнётся.
- `agents.oauth_client_id` и пустая `grants` — Wave 2 OAuth-токен привязан к `agent_connection_id`; без колонки — миграция и backfill.
- `devices.pubkey`, `cert_fpr` — V2 §4.3 (Ed25519/TOFU); Wave 3 анти-подмена без них невозможна.
- `audit_events.prev_hash/hash` — V2 §5.6; добавить nullable сейчас дешевле, чем перестраивать аудит.
- `usage_events` (raw) или histogram — p50/p95 из суточных сумм не считаются (F-13).
- `sessions` → `account_sessions` (F-07) — иначе коллизия с MCP-сессией.

Отдельно: `AgentConnection` в Wave 1 создаётся из `clientInfo.name` без подтверждения на устройстве; в V2 первый коннект требует approval (V2:916-920). Это осознанный перенос, но его надо записать как принятый риск (link-key утечка = доступ до отзыва), а не как «модель уже готова».

### A3. Per-agent link-key: где хранить, как резолвить, legacy, состояния и гонки

- **Где хранить:** авторитет — registry-запись узла (`agents: [{id, key_sha256, mode, scopes, workspace, revoked_at}]`) или отдельный `keys.json`; БД — проекция для кабинета. Причина: `verify_token` — горячий путь MCP; он не должен зависеть от `account.db` и от pip-зависимостей кабинета (F-05). Это отклонение от V2 §3.4 («registry импортируется в БД, обратная запись только для отката») — зафиксировать в ADR-006 как обратное направление на время Wave 1.
- **Как резолвить:** новый `authenticate(request) -> AuthContext(user, agent_id, key_kind, source)`; порядок: device token в header → agent key (`ak_…`, по `sha256` в registry) → legacy `link_key` (plaintext, только query) → иначе 401. Обязательная кросс-проверка: `agent.node_name == ?user=` (F-05/REV-AGT-13). `verify_token` остаётся обёрткой для 675 существующих тестов.
- **Legacy `link_key`:** не удалять и не хешировать в Wave 1. Оставить plaintext в registry (он и сегодня там, `gateway.py:352-357`), при миграции создать агента `default` с `sha256(link_key)`; ротация legacy-ключа обновляет и хеш `default`. Удаление plaintext — только Wave 2, после того как все клиенты перевыпустят ключи.
- **Состояния:** `absent → active → rotating → revoked`; у ключа — `created_at`, `last_used_at`, `revoked_at`, `grace_until` (если нужна ротация без разрыва); у агента — `revoked_at`.
- **Гонки:**
  - два `initialize` с одним ключом → `INSERT … ON CONFLICT(key_sha256) DO UPDATE last_used` (без дубля);
  - `initialize` с отозванным ключом → 401, запись не воскресает;
  - ротация во время вызова → вызов живёт на старом `agent_id`, новый ключ действует со следующего; grace задан явно;
  - отзыв во время вызова → in-flight по политике (`kill_on_downgrade`-аналог), новые — 401;
  - ключ узла A с `?user=B` → 401;
  - параллельный `initialize` при первичном создании — unique constraint + повтор чтения.

### A4. Привязка кодом: где код, кто создаёт, соотношение хранилищ, неописанные сценарии

**Сейчас (код):** `pending_pairs` — in-memory dict в процессе шлюза, TTL 10 мин, cap 200; креды создаются только по admin-approve и отдаются один раз через `GET /v1/devices/pair/{code}`. **В плане:** код создаёт узел (`POST /v1/devices/pair` с device token), вводит человек в кабинете, `device_claims` — таблица в БД, registry получает `device_id/tenant_id`. Итого **четыре хранилища** на один поток: `pending_pairs`, `device_claims`, `registry.json`, `devices` — без описанного жизненного цикла (F-01/F-16). Рекомендация: одно состояние — в БД (`claims` с `state`, `pair_secret_hash`, `node_name`, `expires_at`, `claimed_by`, `claimed_at`, `collected_at`), а `pending_pairs` оставить только как legacy-совместимость или убрать; узлу — `GET /v1/devices/me`, а не публичный статус по коду.

**Кто создаёт:** узел/CLI (`rhm pair`) с device token; код показывается локально (консоль/трей). Кабинет только вводит код. Admin-approve остаётся для старых узлов и/или как второй фактор.

**Не описаны сценарии:** офлайн узла после выдачи кода; повторный claim тем же аккаунтом (идемпотентность?); второй аккаунт на тот же код/устройство; устройство уже привязано (потеря аккаунта, восстановление); узел переименован авто-суффиксом (`gateway.py:812-823`) между стартом и claim; узел отозван/удалён до claim; коллизия кода с admin-паррингом; регистр/дефисы в коде; два браузера одновременно; истечение TTL в момент claim; ротация device token между стартом и claim; claim при живом вызове/сессии агента.

### A5. Режим/scopes: достаточно ли трёх уровней? Старый узел, mixed-версии, падение моста

**Трёх уровней в Wave 1 нет** (F-06). Есть: мост (может отфильтровать) и узел (может применить ctx). «Токен» как уровень появится только с JWT (Wave 2) — link key не содержит claims. Для старого узла без ctx: он просто проигнорирует `ctx` (его цикл читает `params.name/arguments`), значит **вся защита обязана быть на мосте**; узел останется с глобальным `MESH_READ_ONLY`. Это надо назвать явно и протестировать (T-REV-20).

**Mixed-версии:** новый узел + старый мост — ctx не приходит, узел обязан брать глобальный конфиг (fail-closed к `MESH_READ_ONLY`, не «разрешить всё»); старый узел + новый мост — ctx игнорируется, мост фильтрует; новый узел + новый мост — ctx применяется на обоих. Нужна версия протокола в `hello` и явная таблица «кто что гарантирует» (ADR).

**Падение моста посередине вызова:** узел доигрывает и пишет результат в мёртвый сокет, шлюз фейлит future и **повторяет вызов** (`gateway.py:592-614`) — мутация может выполниться дважды. Нужен `call_id` + дедуп на узле (F-32), статус `lost` в журнале и явная политика «клиент видит ошибку, узел мог выполнить» (F-13).

**Scopes:** словари W1 и V2 различаются (`files:read` vs `device:read`, `shares:manage` vs `files:share`, нет `admin`); при Wave 2 токены будут нести V2-scopes, а кабинет хранит W1. Выбрать один словарь сейчас или таблицу маппинга (REV-ARC-02). Также не сказано, что важнее при `mode=agent` и нехватке scope — AND (оба) или mode override; зафиксировать.

### A6. Локальный журнал и отправка: где утечка/расхождение, инварианты

**Утечки:** `agent_label` (управляется клиентом) в UI/CSV; точный `ts` и `bytes` — метаданные; CSV-формулы; ошибки могут содержать путь/команду, если `error_code` не enum, а текст; outbox содержит `device_id` и счётчики и уходит по TLS — но с какой аутентификацией, не сказано (F-04). «Тест-сканер на секреты» проверяет один кейс `cat secret`; нужен property-based тест на отсутствие подстрок аргументов и запрет свободного текста.

**Расхождения:** (1) отказ на мосте не доходит до узла; (2) retry после реконнекта; (3) узел выполнил — результат потерян (или наоборот); (4) смена часов; (5) ротация/очистка во время записи; (6) перепривязка устройства (tenant); (7) переименование агента меняет исторический label; (8) обрезка ответа на мосте меняет `bytes`.

**Инварианты, которые надо зафиксировать:**
1. Узел — единственный писатель и источник истины; агрегаты — производные, никогда не «авторятся» отдельно.
2. Один `call_id` = ровно одна запись журнала; повторы дедуплицируются.
3. `status ∈ {ok, error, denied, cancelled, lost}`; `denied` на узле — только отказы узла; отказы моста — отдельный счётчик `denied_at_bridge`.
4. `bytes` — с узла, до обрезки; `ts` UTC ISO-8601 мс; `error_code` — enum.
5. Никаких `args/paths/output/tokens` по построению: allowlist-сериализатор, а не «мы не передаём».
6. Ingest: device-token + HMAC/nonce, `tenant_id` только серверный, `day_revision` монотонный, upsert «не перетирать новее старым», лимит тела, будущие дни — reject.
7. Ретенция/purge: удаление аккаунта и перепривязка чистят агрегаты; outbox ограничен и очищается.
8. Отправка по умолчанию — только после явного согласия при claim (для неподтверждённых устройств — off), тумблер останавливает весь egress (проверка packet capture).
9. Один путь (`core/paths.config_dir()`/`MESH_STATE_DIR`), 0600/ACL, один writer, lock на ротацию.
10. Экспорт CSV: санитизация формул, UTF-8 BOM, детерминированный порядок.

### A7. Противоречия Wave 1 ↔ V2 (1.7, 7) и пробелы порядка работ

1. **Порядок/границы:** V2 §1.6 п.6 требует «разрезать `gateway.py` на `bridge/`» и F-ворота (V2:1250) — Wave 1 вместо этого монтирует `account/` в монолит и откладывает `bridge/`. Либо V2 §1.6/7.1 обновить (F = F1 в монолите + F2 разрез), либо Wave 1 нарушает собственные ворота.
2. **Wave 1 «экономит 2–3 недели»** (V2:98) при том, что полный план и так 20–22 недели, а MVP после Wave 1 — ещё 12–13 (V2:60): суммарно ~19–20 недель, экономия неочевидна; пересчитать календарь.
3. **API:** W1 `/account/api/*` vs V2 `/v1/account/*` (V2:653-667); E4 зависит от E8 (V2:686), которого в Wave 1 нет.
4. **Имена сущностей:** `sessions` (W1) vs `Session` = MCP-сессия (V2:236) — F-07.
5. **Scopes:** два словаря (W1 Приложение C vs V2 §4.2:253-264).
6. **Флаги:** `MESH_ACCOUNT` vs `RHMV2_ACCOUNTS` (V2:220) — F-33.
7. **Link-key агент:** W1 `default` из registry vs V2 E8.2 «label из clientInfo + approval на устройстве» (V2:898-920); approval в Wave 1 нет — принятый риск, не «модель готова».
8. **Tenant:** личный тенант W1 vs org/`plan`/`bridge_id`/`data_region` (V2:230-233); нет membership.
9. **Направление registry↔DB:** W1 пишет `device_id/tenant_id` в registry (W1:402), V2 — импорт в БД и обратная запись «только для отката» (V2:219, 299) — F-05.
10. **Identity устройства:** V2 Ed25519 + подпись `hello` + TOFU (V2:273-276); W1 claim — только код, `pubkey` нет.
11. **Usage:** W1 суточные суммы vs V2 события/часы и p50/p95 (V2:637) — F-13.
12. **Audit:** W1 простая строка vs V2 hash-chain/чекпоинты (V2:404-406); колонки не заложены.
13. **Удаление аккаунта:** W1 каскад vs V2 tombstone (V2:302) — определить, что удаляется, что остаётся в аудите.
14. **Edge/self-host:** кабинет с Google-секретом в монолите, который публикуется `deploy_gateway.sh` — против V2 §3.2/E11 (F-28).
15. **Порядок спринтов:** claim (Sprint 1) зависит от показа кода (трей — Sprint 3, CLI нет) — F-17; usage `agent_id` (Sprint 2) зависит от модели агентов (Sprint 2, ок) и ctx (Sprint 3) — журнал до ctx будет писать `legacy`; `read_only` по умолчанию для агентов появляется в Sprint 2, а принуждение на узле — в Sprint 3: месяц агенты «read_only» без узлового принуждения. Добавить `REV-MOD-09` в Sprint 2 или перенести дефолт.

### A8. WBS-блоки и владельцы

Предлагаемая структура (сохраняя существующие 111 строк): `AUTH-01…18`, `CLM-01…14`, `CAB-01…16`, `AGT-01…13`, `USE-01…18`, `CTL-01…10`, `TRY-01…11`, `MOD-01…10`, `SEC-01…10`, `OPS-01…13` + новые `ARC-01…02`, `API-01`, `PERF-01…02`, `DOC-01`. Владельцы и ревьюеры — §5. Критический путь: `AUTH-01 → AUTH-02…15 → CLM-01/13 → CLM-12 → AGT-01/12 → MOD-09/10 → CTL-04 → TRY-02`; `USE-01…18` идут параллельно Sprint 1, но ingest (`USE-14`) — после `CLM-12` и `REV-USE-16`. Без `ARC-01/02` и `API-01` Sprint 0 не имеет выхода и G0 не закрывается.

---

## Приложение B. Минимальная структура `account/` и точки интеграции

```
account/
  __init__.py          # build_app(); НЕ импортирует gateway
  config.py            # AccountConfig.from_env(); проверка base_url/secret/dev; DeploymentMode
  app.py               # Starlette sub-app: routes, middleware, templates env
  middleware.py        # security headers, CSRF, Origin, request-id, no-store
  db.py                # connect(): WAL, foreign_keys=ON, busy_timeout; run_migrations()
  migrations/0001_init.sql, 0002_*.sql, down/
  repo.py              # тонкие репозитории; все запросы параметризованы и tenant-scoped
  security.py          # cookie signer, CSRF, constant-time compare, rate limit (account+IP)
  auth/google.py       # start/callback, JWKS cache, state/nonce/PKCE store
  auth/login.py        # pre-login сессия, state TTL, привязка к cookie
  claim.py             # state machine issued→claimed→collected→expired; pair_secret
  routes/pages.py      # /, /devices, /agents, /usage, /settings (Jinja)
  routes/api.py        # /v1/account/* (session, claim, devices, agents, usage)
  routes/ingest.py     # /v1/nodes/usage (device-token + HMAC; без cookie/CSRF)
  templates/...
  static/...           # css/js/i18n (.js или Jinja, не .json — F-19)
  sync.py              # идемпотентный reconciler registry ↔ DB (фон, не в запросе)
  hooks.py             # Protocol BridgeHooks (единственный шов)
```

**Точки интеграции в `gateway.py` (и только они):**

1. **Сборка маршрутов (низ файла, до `routes`):**
```python
def build_account_mount():
    if not ACCOUNT_ENABLED:            # MESH_ACCOUNT
        return None
    try:
        from account.app import build_app
    except ImportError:
        logger.error("MESH_ACCOUNT=1 but account dependencies are missing; cabinet disabled")
        return None
    return Mount("/account", app=build_app(hooks=_bridge_hooks()))

# routes.insert(len(routes) - 1, mount)  # строго ПЕРЕД catch-all /{user}/{rest:path}
```
2. **`authenticate(request) -> AuthContext`** (новый; `verify_token` — обёртка): резолв device/agent/legacy ключа, `agent_id`, `source`.
3. **`initialize`:** после `issue_session_id` — `hooks.upsert_agent(user, client_info, auth_ctx)` + `sessions_by_agent[agent_id].add(session_id)`.
4. **`tools/call`:** `ctx = hooks.enforce(auth_ctx, name)`; при отказе — `mode_denied/scope_denied` **до** `call_remote_tool`; `params["ctx"]` (forward-compatible: старый узел игнорирует).
5. **Claim:** новые `/v1/devices/claim/start|status` (device token) + `GET /v1/devices/me`; старые `/v1/devices/pair*` не трогать.
6. **Отзыв:** `hooks.close_agent_sessions(agent_id)` и `hooks.patch_registry(node, {...})`.
7. **Health/кабинет:** `hooks.online(node_names)` — из `active_tunnels` без внутреннего HTTP.

**`BridgeHooks` (7 методов, не 3–4):** `authenticate_credential(request)`, `upsert_agent(user, client_info, ctx)`, `agent_settings(agent_id)`, `enforce_call(ctx, tool)`, `close_agent_sessions(agent_id)`, `online_nodes(names)`, `patch_registry(node, fields)`.

**Middleware:** `account` — свой sub-app; родительский CORS не должен добавлять ACAO для `/account` (кастомный ASGI-фильтр), внутри — CSP/XFO/Referrer-Policy/no-store, CSRF на POST, лимиты. `SessionMiddleware` глобально не ставить.

**БД:** все вызовы через `run_in_threadpool`; один writer; миграции — до старта, с backup и down-файлами; `schema_version`; `PRAGMA foreign_keys=ON` на каждом соединении.

---

## Приложение C. Решения, которые нужны до старта (предложение контроллеру)

1. **R-005.** `account_sessions` для логинов; `mcp_sessions` зарезервировано за V2 — *почему:* коллизия имён; *цена ошибки:* миграция Wave 2/3.
2. **R-006.** Registry (или `keys.json`) — авторитет MCP-ключей, БД — проекция; обратное направление к V2 §3.4, зафиксировать как долг Wave 1 — *почему:* горячий путь и graceful degradation; *цена ошибки:* MCP зависит от кабинета.
3. **R-007.** Claim = код + `pair_secret` узла; collection требует device token; для legacy-узлов admin-approve сохраняется — *почему:* иначе кража кода = захват узла; *цена ошибки:* Critical-инцидент.
4. **R-008.** Legacy `default` получает `agent` с явным баннером (или read-only с чек-листом) — *почему:* апгрейд не должен ломать работающие сценарии; *цена ошибки:* регресс у всех существующих.
5. **R-009.** `/v1/account` — публичный контракт кабинета, OpenAPI владелец API — *почему:* Wave 2/3; *цена ошибки:* ломка клиентов/переписывание.
6. **R-010.** `tenant_id` в usage, purge при смене владельца; `call_id` идемпотентность — *почему:* утечка статистики и двойной учёт; *цена ошибки:* данные чужого аккаунта в кабинете.

---

**Итог:** 6 Blocker, 12 Critical, 14 Major, 5 Minor. Направление и границы Wave 1 жизнеспособны; блокеры закрываются в Sprint 0 решениями R-005…R-010 и шестью новыми строками WBS. После этого — scoped re-review только по изменённым строкам (AGENT_TEAM §8.5).
