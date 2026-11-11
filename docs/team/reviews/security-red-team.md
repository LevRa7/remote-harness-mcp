# Security / Auth Red Team — Wave 1 «Аккаунт и контроль»

**Роль:** SEC (Security / Auth), red team
**Дата:** 2026-11-11
**Объект ревью:** `docs/wave-1-auth-account-log-settings.md`, `docs/remote_harness_mcp_v2_plan.md` §3.2/§4/§5/E3/E4/E8/§9, `gateway.py` (v0.4.8), `core/mcp_tools.py`, `core/server.py`, `core/agent.py`, `core/redact.py`, `ops/nginx/remote-harness-mcp.conf`.
**Режим:** только чтение. Реализации Wave 1 нет (`account/`, `core/usage.py`, `core/control.py`, `tests/fake_oidc.py` отсутствуют — проверено листингом репозитория), поэтому всё, что касается кабинета, claim, control API и журнала, — ревью **проекта**, а не кода. Всё, что касается текущего шлюза/узла, — ревью **кода** с указанием строк.
**Важно:** артефакты команды (`WBS.md`, `OWNERSHIP_MATRIX.md`, `TEST_PLAN.md`, `REVIEW_LOG.md`, `VERIFICATION_PROTOCOL.md`) появились в 04:29–04:31, уже после начала ревью. §4–§7 сверены с ними, `tools/check_team_coverage.py` → «Coverage complete» (111/111). Пункт PB-01 о их отсутствии снят по факту, его место заняли содержательные находки.

---

## 1. Вердикт

**NOT_READY.**

План описывает правильные намерения (OIDC, `__Host-` cookie, claim по коду, три уровня принуждения режима, метаданные без payload), но не задаёт **границ доверия для нового локального и облачного контура**, а два его ключевых предохранителя уже не работают в текущем коде: `MESH_READ_ONLY` не распространяется на `bash_exec`/`run_job`, а шлюз вообще не различает агентов и режимы (критерий MOD-02/MOD-05 в WBS есть, реализации — нет, и по плану она приедет только в Sprint 3, после переключателя режима в кабинете). Плюс пять мест, где схема имеет форму IDOR/CSRF по умолчанию: claim без привязки к аккаунту и с выдачей credentials по одному коду, ingest агрегатов с клиентским `device_id`, control API без Origin/Host и Windows-DACL, link-key в plaintext в world-readable реестре, прямой вызов узла с `Access-Control-Allow-Origin: *` и отключаемой аутентификацией. Процессный гейт теперь зелёный, поэтому вердикт держится на четырёх Blocker'ах кода/дизайна и трёх гейтах G1/G2/G5, которые нельзя закрыть постфактум.

---

## 2. Модель угроз Wave 1

### 2.1. Активы

| ID | Актив | Где живёт | Ущерб при компрометации |
| :--- | :--- | :--- | :--- |
| A1 | Google-сессия пользователя (`__Host-rhm_session`) | cookie браузера + `sessions` | Полный контроль кабинета: claim/переименование/отзыв агентов, статистика, удаление аккаунта |
| A2 | Привязка устройство↔аккаунт (`devices`, `device_claims`) | SQLite + `registry.json` | Чужое устройство в своём тенанте (чтение/запись файлов, shell) или «потеря» своего |
| A3 | Device token узла | конфиг узла, WS-туннель | Открыть туннель под чужим именем узла → полный доступ к машине |
| A4 | Link-key / per-agent key | URL в коннекторе Gemini, `registry.json` | Вызовы инструментов на узле от имени агента (в обход кабинета) |
| A5 | Mode/scopes агента | `agents`, `ctx` в туннеле | Обход read-only: shell, запись, камера/микрофон, шары, `mesh_update` |
| A6 | Локальный журнал (`usage.jsonl`) и агрегаты | устройство + кабинет | Утечка метаданных (что и когда делали), отравление статистики |
| A7 | Control API (loopback) | `control.token` + localhost | Смена режима/рабочей папки, очистка журнала, чтение состояния |
| A8 | `registry.json`, `account.db`, `control.token`, `MESH_SESSION_SECRET` | ФС шлюза/узла | Массовая компрометация всех узлов/сессий |

### 2.2. Противники

| ID | Противник | Возможности | Цель | Реалистичность |
| :--- | :--- | :--- | :--- | :--- |
| P1 | Аноним из интернета | Сканеры, брутфорс `/v1/devices/pair`, `/api/register`, `/health` | Завести узел, угадать код, перечислить имена, DoS | Высокая (эндпоинты публичные) |
| P2 | Владелец украденной cookie | Браузер жертвы или копия cookie | Захватить сессию, привязать свой узел, отозвать чужие агенты | Высокая (30 дней, без step-up и без idle-TTL) |
| P3 | Чужой залогиненный аккаунт | Свой Google-аккаунт, полный доступ к `/account/api/*` | IDOR: чужой код claim, чужой `device_id` в ingest, чужие `agent_id` | Высокая (схема БД не содержит проверок владельца) |
| P4 | Злой/заражённый пользователь устройства | Локальный процесс, другая учётка Windows, вредоносная страница в браузере | Прочитать `registry.json`/`control.token`, дернуть `127.0.0.1:8096`, снять read-only | Высокая (см. SEC-07/09/14) |
| P5 | Скомпрометированный мост/хостинг | Root на шлюзе, БД, логи, `MESH_ADMIN_TOKEN` | Читать метаданные, выдавать себя за агента/устройство, отравлять статистику | Средняя, но именно её закрывает §5 (и она вне Wave 1) |
| P6 | Украденный/подсмотренный код привязки | Скриншот, демонстрация экрана, плечо | Собрать credentials раньше устройства (`GET /v1/devices/pair/{code}`) | Средняя (код = единственный секрет, не привязан к устройству) |
| P7 | Инсайдер/оператор | Прямой доступ к ФС/БД | Стёрть аудит, подменить статистику, отозвать чужие агенты | Средняя (аудит без хеш-цепочки, файлы без DACL) |

### 2.3. Границы доверия

| ID | Граница | Что пересекает | Доверие в Wave 1 |
| :--- | :--- | :--- | :--- |
| TB1 | Интернет ↔ `gateway.py` | HTTP/SSE/MCP, `?user=`+`token=`, `/api/register`, `/v1/devices/pair` | **Недоверенный вход**, аутентификация только по секрету в URL/заголовке |
| TB2 | Браузер ↔ `/account` | cookie, CSRF-токен, `next` | Недоверенный вход, доверие после OIDC-сессии |
| TB3 | Шлюз ↔ узел (WS-туннель) | `tools/call` c `ctx`, device token | Узел доверяет шлюзу **без подписи и без привязки к агенту** (SEC-05) |
| TB4 | Браузер/локалка ↔ control API | loopback HTTP + SSE + токен | Требует явной модели: localhost ≠ доверенный (SEC-14) |
| TB5 | Любая страница ↔ `127.0.0.1:8096` | JSON-RPC POST | **Сейчас недоверенная сторона может вызвать shell** (SEC-07) |
| TB6 | Узел ↔ локальная ФС | workspace, read roots, `bash_exec` | `bash_exec` = полный shell пользователя; `workspace` — не граница (SEC-08) |
| TB7 | Шлюз ↔ Google | code/ID token/JWKS | Внешний IdP, доверенный, но проверки обязаны быть полными (SEC-02) |
| TB8 | Шлюз ↔ БД/FS | `registry.json`, `account.db`, логи nginx | Доверенный периметр, но файлы читаемы локально (SEC-09) |

### 2.4. Три главных пути атаки

1. **P4→shell:** любая открытая страница → `POST text/plain` на `127.0.0.1:8096/mcp` → `bash_exec` (токен по умолчанию пуст) → RCE. Сейчас это работает; Wave 1 добавляет рядом второй loopback-сервис с той же ошибкой проектирования.
2. **P2→A2:** украденная cookie (30 дней, без step-up, без idle-TTL, `sessions.id` = сам идентификатор) → `POST /account/api/claim` с кодом → своё устройство/чужой код в аккаунте жертвы, либо отзыв всех её агентов.
3. **P3→A6:** `POST /account/api/usage/ingest` с клиентским `device_id`/`agent_id` → перезапись статистики чужого устройства (ключ идемпотентности = клиентские поля, подписи нет), либо отравление своей, чтобы скрыть активность.

---

## 3. Findings

Формат: `ID | severity | область | проблема | доказательство | рекомендация | владелец`. Пункт **PB-01** (отсутствие артефактов команды) снят в ходе ревью: файлы появились, `tools/check_team_coverage.py` даёт «Coverage complete» (111/111); его заменили содержательные SEC-находки.

### 3.1. Blocker

| ID | severity | область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-04** | Blocker | Режимы | `MESH_READ_ONLY` — «старший предохранитель» — не покрывает `bash_exec` и `run_job`: в их обработчиках проверки нет вообще. Значит read-only узел уже сейчас выполняет произвольные команды; тест «read-only нельзя обойти» упадёт на первом же вызове | `core/mcp_tools.py:1847-1885` (`_tool_bash_exec`, без guard), `:2553-2661` (`_tool_run_job`, без guard); guard есть только в `:428-434` (`_write_guard`), `:873`, `:1316-1321`, `:2770`, `:2878`, `:2886`. План: `docs/wave-1...:280` («глобальный MESH_READ_ONLY — старший предохранитель»), `:298` («Read-only нельзя обойти») | Немедленный точечный патч (не ждать рефактора на `SessionContext`): guard в `_tool_bash_exec`, `_tool_run_job`, `_tool_unshare`; тест-матрица «инструмент × MESH_READ_ONLY» в CI. Потом — единый `policy.evaluate(tool, ctx)` | BE + SEC |
| **SEC-05** | Blocker | Режимы/scopes | Шлюз не различает агентов, режимы и scopes: `verify_token` только аутентифицирует узел, `tools/list` отдаётся статическим списком (все инструменты всем), в `tools/call` нет ни одной проверки mode/scope; `?user=` выбирает узел, но не агента. В WBS критерии уже есть (MOD-01…06), но по плану принуждение — Sprint 3, а переключатель режима в кабинете — Sprint 2: две недели «права есть только в UI» | `gateway.py:494-530` (`verify_token`), `:1473-1479` (`tools/list` — статический массив), `:2414-2512` (`tools/call` без policy), `:2982-3007` (аннотации — только подсказки); `docs/team/WBS.md:59` (CAB-07 зависит от MOD-01), `:137-144`; план `docs/wave-1...:280-284`, Sprint 2/3 | Ввести `resolve_agent(credential) → ctx` и фильтрацию `tools/list`/`tools/call` **до** CAB-07/AGT-09 (см. SEC-117), либо выпускать Sprint 2 с выключенным переключателем режима | BE + SEC |
| **SEC-07** | Blocker | Прямой вызов узла | Локальный MCP-сервер узла: `Access-Control-Allow-Origin: *`, нет проверки `Origin`/`Host`, нет проверки `Content-Type`, аутентификация **выключена**, если `MESH_TOKEN` не задан (дефолт). Любая открытая страница может `POST` простым `text/plain`-запросом на `http://127.0.0.1:8096/mcp` и выполнить `bash_exec` (DNS rebinding/CSRF → RCE), а любой локальный процесс — прочитать vitals без токена | `core/server.py:707-710` (`ACAO: *`), `:751-779` (`_token_valid`/`_authorized`: пустой токен → True), `:781-791` (тело читается без проверки Content-Type), `:840-848` (принимаются `/messages`, `/sse`, `/mcp`), `:676-679` | Токен обязателен всегда (генерируется при установке), `Origin`/`Host` allowlist (127.0.0.1/localhost), требовать `application/json` (иначе preflight), убрать `ACAO: *` для мутирующих ответов, `Vary: Origin`. Закрыть **до** работ по control API, чтобы не скопировать шаблон | BE + DESK |
| **SEC-10** | Blocker | Claim | Проектируемый claim не привязан к тому, кто его выполняет: `pending_pairs` — глобальный in-memory словарь без `account_id`/`tenant_id`, а `GET /v1/devices/pair/{code}` отдаёт выданные credentials **любому**, кто знает код (то есть подсмотревшему экран), в обход кабинета. Кроме того, `device_pair_approve` всегда создаёт **новую** запись с новыми ключами — прямое противоречие плану «привязка к существующему узлу без выдачи новых ключей» (наследный узел получит имя `X-2` и станет фантомом) | `gateway.py:896` (`pending_pairs`), `:940-950` (нет владельца), `:963-980` (выдача credentials по коду; `attempts` инкрементится и никогда не проверяется), `:1012-1016` + `:1009-1010` (`unique_node_name`), `:984-989` (approve требует только `MESH_ADMIN_TOKEN`); план `docs/wave-1...:150` («к существующему узлу, без выдачи новых ключей»), `:152` | `device_claims`: `account_id`, `tenant_id`, `node_name`, `pair_id`, `attempts`, `claimed_at`, `used_at`; одноразовое `UPDATE ... WHERE used_at IS NULL`; код привязывать к отпечатку устройства (device token hash), а не только к знанию кода; credentials выдавать только устройству (по device token), не по коду; approve — от аккаунта, не от админ-токена | BE + SEC |

### 3.2. Critical

| ID | severity | область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Critical | Сессии | `sessions.id` — первичный ключ **и** значение cookie, TTL 30 дней, без idle-таймаута, без `revoked_at`, без привязки к отпечатку при проверке. Дамп БД/бэкап = угон всех сессий на месяц; «отозвать все сессии» в схеме нечем реализовать | `docs/wave-1...:319-327` (схема `sessions`), `:147` (cookie/TTL), `:500` (TTL/ротация); `ip_hash`/`ua_hash` пишутся, но политика не описана | Cookie = `secrets.token_urlsafe(32)`, в БД `sha256(id)` (как у device token, `gateway.py:361-364`); + `revoked_at`, idle 12 ч + absolute 30 дней, `rotated_from`; ротация при login/claim/step-up; revoke-all; БД 0600 | SEC + BE |
| **SEC-02** | Critical | OIDC | Список проверок неполон: нет allowlist `alg` (RS256 only, запрет `none`), нет `azp` при нескольких audience, нет требования одноразового серверного `state` с TTL и привязкой к pre-session, нет явной привязки PKCE `code_verifier` к `state`, нет политики JWKS (refresh по неизвестному `kid` с лимитом, fail-closed при недоступности Google), не описано слияние аккаунтов (`sub` vs `email`) | `docs/wave-1...:145`, `:165-168` (тесты), `docs/remote_harness_mcp_v2_plan.md:942` (google.py); фактически кода нет (`MESH_SESSION_SECRET`/`MESH_AUTH_MODE` в `.py` не встречаются — проверено grep) | Явный профиль проверки в ADR-004 + единая функция `verify_id_token(...)`: `algorithms=["RS256"]`, `iss="https://accounts.google.com"`, `aud=client_id`, `azp`, `exp/iat/nbf` с skew 60 с, `nonce` из серверного store, `email_verified`; JWKS-кэш с negative-cache и лимитом refresh; при ошибке JWKS — отказ, не приём | SEC |
| **SEC-03** | Critical | OIDC/редирект | «`next` — только относительный путь из allowlist» без канонизации: `//evil`, `/\evil`, `%2f%2fevil`, `..`, `?next=javascript:` и подмена `next` в cookie/состоянии. Если `next` хранится в cookie без подписи — open redirect + возможный захват OAuth-кода | `docs/wave-1...:159`, `:171`; общая мера `:502` | `next` хранить **в серверной записи `state`**, а не в cookie; принимать только `^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$`, декодировать один раз, отбрасывать `//`, `\`, `%2f%2f`, `http:`, `javascript:`; итог — 302 на `/account/` | SEC + API |
| **SEC-06** | Critical | Совместимость/режимы | Заявленный тест «старый узел без контекста должен работать в глобальном режиме» — это спроектированный обход: узел 0.4.x (или узел, совравший в `hello` о версии) получает полные права, хотя в кабинете у агента `read_only`. Понижение режима на таком узле не применяется никогда | `docs/remote_harness_mcp_v2_plan.md:598` (тест), `:593` (`hello` с `mode`, обратная совместимость), `docs/wave-1...:284` | Политика: нет `ctx` + `MESH_ACCOUNT=1` → мутирующие вызовы `deny` (`policy_unavailable`), узел в кабинете помечается `degraded`; version allowlist для мутаций; `read_only` для старого узла — форс на шлюзе по статическому списку инструментов | BE + SEC |
| **SEC-08** | Critical | Scopes/ФС | `read_only`/`files:read` не ограничен рабочей папкой: `_resolve` принимает абсолютный путь как есть, поэтому read-only агент читает `~/.ssh/id_rsa`, `~/.aws/credentials`, профиль браузера, `agent.env` с `MESH_TOKEN`. План продаёт read-only как безопасный режим, но это не граница конфиденциальности | `core/mcp_tools.py:403-412` (`_resolve`: абсолютный путь → `realpath` без проверки корня), `:428-434` (`_write_guard` — только запись), `:1892-1943`, `:2092-2220` (grep/glob без корней); план `docs/wave-1...:650` (`files:read` = 4 инструмента) | Ввести `read_roots` + deny-list (`.ssh`, `.aws`, `.gnupg`, `agent.env`, `*.pem`, `id_*`) и проверять в `read_file`/`list_dir`/`grep_search`/`glob_find`; в документации честно: `files:read` = чтение в пределах read roots, `bash_exec` (в `agent`) = весь пользователь | BE + DOC |
| **SEC-09** | Critical | Ключи/ФС | Link-key хранятся в plaintext и в world-readable файле: `save_registry` пишет `open(..., "w")` без `chmod`/umask, каталог создаётся `makedirs` с дефолтом; ключ возвращается в каждом ответе `/api/register` (перерегистрация) и `/rotate`. «Показывается один раз, в БД только хеш» не выполняется; запись неатомарна и без lock → гонка read-modify-write (потеря ротации/воскрешение старого ключа), а битый JSON читается как `{}` | `gateway.py:314-317` (`save_registry`), `:305-312` (`load_registry` → `{}` при ошибке), `:57` (`/etc/remote-harness-mcp/registry.json`), `:352-357` + `:689` + `:863` + `:1059` (plaintext link key, выдача при re-register/rotate) | Хранить `sha256(link_key)` (+ `prefix` для UI), plaintext показывать только в момент выпуска; `chmod 0600`/DACL, атомарная замена (tmp+`os.replace`)+`fcntl`/`msvcrt` lock; при ошибке парсинга — fail-closed (отказ, не `{}`); миграция `0002_hash_link_keys` | BE |
| **SEC-11** | Critical | Usage/ingest | `POST /account/api/usage/ingest` не имеет описанной аутентификации устройства, а ключ идемпотентности `(device_id, agent_id, day, tool)` целиком клиентский. Любой узел/аккаунт может писать строки за чужой `device_id`, перезаписывать числа и «обнулять» активность; подписи/MAC и окна replay нет | `docs/wave-1...:215` (ingest), `:222` (идемпотентность), `:367-378` (PK без `tenant_id`), `docs/remote_harness_mcp_v2_plan.md:648` (`bridge/usage.py`) | Аутентификация: тот же device token, `device_id` берётся **из аутентификации**, а не из тела; HMAC агрегата ключом устройства; `agent_id` проверяется на принадлежность устройству; день — не в будущем и не старше retention; merge по `max()` для счётчиков, не «перезапись» | BE + SEC |
| **SEC-14** | Critical | Control API | «loopback HTTP + SSE, токен в `control.token` (0600)» — недостаточная модель: loopback на Windows общий для всех локальных учёток, `0600` — POSIX-понятие (нет DACL/DPAPI), нет проверки `Host` (DNS rebinding: `evil.com` → 127.0.0.1) и `Origin`, не требуется `Content-Type: application/json` → `PATCH /v1/settings` (`default_mode`, `workspace`) и `DELETE /v1/usage` CSRF-ятся с любой страницы; CORS не специфицирован | `docs/wave-1...:274-276`, `:288`; риск `docs/remote_harness_mcp_v2_plan.md:1390` («Локальный control API»); по тому же шаблону уже сделан `core/server.py:707-710` | Токен обязателен, читается из файла с явной Windows DACL (`icacls`) или DPAPI; `Host` ∈ {127.0.0.1, localhost} и `Origin` allowlist (иначе 403); JSON-only + `X-Control-Token` (не cookie); SSE только GET; токен ротируется при старте и при смене пользователя; single-instance lock | DESK + SEC |
| **SEC-18** | Critical | Режимы/scopes: обход через соседние инструменты | Даже при закрытом `bash_exec` мутации достижимы обходными путями, которые план не относит к мутирующим: `share_file`/`serve_dir` публикуют файл в интернет (в матрице это `agent`-only, ок), но `run_job` + `job_kill` и `_http_share` (внутренний, не рекламируемый инструмент) исполняются по имени через `_HANDLERS` без проверки, что имя из allowlist; `core/agent.py` пересылает **любое** имя инструмента, включая `_http_share`, из `tools/call` | `core/mcp_tools.py:3454-3482` (`_HANDLERS`, `_http_share`), `:3485-3498` (`call_tool` без policy), `core/agent.py:592-598` (`handle_tool_call` без allowlist); план `docs/wave-1...:284` | Whitelist допустимых имён на узле **и** на шлюзе по `ctx`; `_http_share` разрешать только по внутреннему каналу (подписанный вызов шлюза), не по общему `tools/call` | BE + SEC |
| **SEC-20** | Critical | Claim/сессия: step-up | Нет step-up для опасных действий: claim, отзыв агента/устройства, удаление аккаунта выполняются только по cookie. Украденная cookie немедленно конвертируется в привязку чужого устройства (P2) и в отзыв всех агентов; §8 требует подтверждения «вводом имени устройства» только для отзыва/удаления, но не для claim | `docs/wave-1...:152` (`claim только для залогиненного`), `:487` (опасные действия), `:500` | Fresh-auth (повторный Google или одноразовый код на e-mail) max 10 мин для claim/revoke/delete; серверный `step_up_at` в сессии; аудит `step_up`; лимит попыток | SEC + UI |

### 3.3. Major

| ID | severity | область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-12** | Major | Журнал/кабинет | Поля журнала наполняются клиентскими строками: `agent_label` из `clientInfo.name`, `tool` из `name` запроса. JSONL экранирует перевод строки, но метка уходит в HTML кабинета и в CSV-экспорт → XSS в кабинете и CSV/formula-injection (`=cmd`, `@`, `+`, `-` в начале значения); длина/алфавит не ограничены | `docs/wave-1...:211` (поля), `:216`, `:213` (`GET .../export.csv`), `:224` (XSS/IDOR-тесты); имена инструментов не валидируются: `core/agent.py:606-607` | Charset+length clamp (например `^[\w .:/-]{1,64}$`), CSV-prefix `'` для `=+-@`, экранирование в шаблонах (Jinja autoescape вкл.), тест-сканер | BE + UI |
| **SEC-13** | Major | Секреты/логи | `redact_secrets` не покрывает новые секреты Wave 1: JWT (`eyJ...`), значения cookie `__Host-rhm_session=...`, `lk_`/будущие `rhk_` вне контекста `key=`, OAuth `code=`, e-mail/PII и Windows-пути. Если §6 «тест-сканер на секреты» переиспользует этот модуль, ID token и cookie окажутся в логах | `core/redact.py:29-56` (7 паттернов), `:24` (не-секреты) | Добавить паттерны JWT, `__Host-*`, `lk_[0-9a-f]{32}`, `code=`, `state=`, `id_token`; отдельный allowlist полей журнала вместо «редактирования после»; CI-сканер по артефактам | SEC + BE |
| **SEC-15** | Major | Admin/tenancy | `MESH_ADMIN_TOKEN` — постоянный god-mode, обходящий тенанты: им можно approve'ить пару, ротировать, отзывать любой узел. В плане Wave 1 он вообще не упомянут, то есть остаётся навсегда вместе с аккаунтами; аудита и срока жизни нет | `gateway.py:894-901` (`admin_authorised`), `:989`, `:1041`, `:1081`, `:1139`; в `docs/wave-1...` упоминания `MESH_ADMIN_TOKEN` нет (проверено grep) | При `MESH_ACCOUNT=1` отключать admin-эндпоинты (или скоупить на тенант + срок + аудит); в плане зафиксировать «admin token умирает вместе с Wave 1» | SEC + OPS |
| **SEC-16** | Major | Rate limit | `client_ip` берёт **первый** элемент `X-Forwarded-For`, который клиент присылает сам; nginx добавляет реальный IP в конец. Все лимиты (`register`, `pair`) ключуются этим значением → обход перебором с рандомным XFF; сама карта `_rate_windows` не ограничена по размеру (утечка памяти) | `gateway.py:726-730`, `:733-750`, `:761`, `:922`; `ops/nginx/remote-harness-mcp.conf:85` (`$proxy_add_x_forwarded_for`) | Брать доверенный IP (правый элемент XFF от доверенного прокси / `X-Real-IP`), allowlist прокси-хопов; TTL-очистка словаря; лимит по аккаунту **и** по коду claim, `Retry-After` | BE + OPS |
| **SEC-17** | Major | Ключи в URL | Link-key как основной транспорт остаётся в URL: nginx-локейшены `/sse` и `/mcp` не отключают `access_log`, значит `?token=lk_...` попадает в combined-лог (`$request`), историю браузера, Referer и настройки коннектора у Google. Плюс `allow_legacy_query` продолжает принимать **device token** в URL до явной `rotate-token`, вопреки §9 | `ops/nginx/remote-harness-mcp.conf:94-136` (нет `access_log off`, в отличие от `:6`, `:10`, `:39`, `:48`), `gateway.py:843-851` (`allow_legacy_query=True`), `:520-527` (legacy device token в query), `core/agent.py:665-670` (`tunnel_uri` всегда с `?token=`); план `docs/wave-1...:508` | `access_log off` (или `log_format` с маскировкой `token`) для `/sse`,`/mcp`,`/ws`; `Referrer-Policy: no-referrer` на `/account`; в плане — явный отказ от legacy-query при `MESH_ACCOUNT=1`; per-agent ключ передавать заголовком, где клиент это умеет | OPS + SEC |
| **SEC-19** | Major | Аудит | Аудит не tamper-evident и неполон: нет `prev_hash/actor_ip`, нет событий на `device_revoke`/`rotate`, аудит можно удалить прямым доступом к БД (нет append-only/прав), `claim_failed` пишется без актора. Для «1 год хранения» и обещания «видно, кто менял режим» этого мало | `docs/wave-1...:380-389` (схема без `prev_hash`), `:153` (список событий), `docs/remote_harness_mcp_v2_plan.md:240` (`prev_hash/hash`), `:302` (ретенция) | Добавить `prev_hash`, `actor_account_id`, `ip_hash`, `ua_hash`, `request_id`; чекпоинт-цепочку хотя бы на уровне суток (полный Merkle — Wave 5); запись только через один writer, файл/БД 0600; отдельный тест «попытка удалить событие» | BE + SEC |
| **SEC-21** | Major | Сессии/CSRF | CSRF-модель описана как «double-submit + Origin» без деталей: не сказано, где живёт CSRF-секрет (cookie читаемая? meta-тег?), как он ротируется при логине, обязателен ли он для `POST /account/logout` и для htmx-заголовков; `SameSite=Lax` защищает не всё (top-level POST-навигация), а `GET /account/api/session` отдаёт данные без CSRF — ок, но `GET`, меняющий состояние, не запрещён явно | `docs/wave-1...:148`, `:170`, `:501` | CSRF-секрет в серверной сессии, в HTML — meta-тег, обязательный заголовок `X-CSRF-Token` для всех не-GET; `Origin`/`Sec-Fetch-Site` проверка; запрет state-changing GET; тест «чужой Origin → 403» | SEC + UI |
| **SEC-22** | Major | Отзыв/туннель | Отзыв не гарантирует прекращения доступа: `device_revoke` закрывает только in-memory туннель, per-agent отзыв вообще не описан механизмом для уже открытых SSE/WS; `verify_token` перечитывает реестр на запрос, но живой туннель не перепроверяется, а watchdog реагирует лишь на состояние сокета (интервал 30 с) → «следующий вызов 401 < 5 с» из E8 не обеспечен | `gateway.py:1131-1155` (revoke), `:3392-3429` (`tunnel_watchdog`: только `client_state`, 30 с), `:2523-2603` (WS без периодической ревалидации); план `docs/wave-1...:292`, `:196` | Ревалидация `(device, agent, revoked_at)` на каждом `tools/call` + периодический ping-контроль (≤5 с для агента с отозванным ключом); при отзыве — закрывать SSE/WS адресно и писать аудит | BE |
| **SEC-28** | Major | Спецификация | TTL сессии противоречив: план — «TTL 30 дней» без idle-таймаута, `WBS AUTH-13` — idle 30 дней / absolute 90, `AUTH-11` хранит session id в БД (см. SEC-01), а §5 плана v2 говорит «sessions — 30 дней». Реализатор выберет одно из трёх, и обещанный «отзыв всех сессий» разъедется с ретенцией | `docs/wave-1...:147`, `:500`; `docs/team/WBS.md:25-27`; `docs/remote_harness_mcp_v2_plan.md:302` | Ruling в `REVIEW_LOG.md`: одно значение (рекомендую idle 12 ч / absolute 30 дней), зафиксировать в ADR-004 и SEC-102; T-AUTH-105/106 сверяют факт с ADR | P + SEC |

### 3.4. Minor

| ID | severity | область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-23** | Minor | Claim UX | `pair_url` указывает на `/pair?code=...`, но такого маршрута нет — человеческий сценарий «код с экрана устройства» даёт 404, а единственный рабочий путь — `approve` с админ-токеном | `gateway.py:954`, таблица маршрутов `:3356-3379` (нет `/pair`) | Либо реализовать `/pair` как часть кабинета (и не смешивать с админ-approve), либо убрать `pair_url` из ответа | UI + BE |
| **SEC-24** | Minor | Схема/приватность | `device_claims.code` — plaintext PK, нет `account_id`/`attempts`; `ON DELETE CASCADE` в схеме декоративен без `PRAGMA foreign_keys=ON` (SQLite по умолчанию OFF) → удаление аккаунта оставляет `devices/agents/usage_daily`, что противоречит §«удаление аккаунта» и privacy-странице | `docs/wave-1...:391-397`, `:321` (`ON DELETE CASCADE`), `:205` (удаление аккаунта), `:400` («доступ через sqlite3»), `docs/remote_harness_mcp_v2_plan.md:302` | `PRAGMA foreign_keys=ON` в каждом соединении + тест каскада; `code_hash` вместо `code`; колонки владельца/попыток; tombstone для аудита | BE |
| **SEC-25** | Minor | Наблюдаемость | `/health` публичный, `ACAO: *`, отдаёт `tunnel_events` (имена узлов) и `streams_per_user` → перечисление узлов для целевого фишинга/claim-атак | `gateway.py:639-663` | Оставить `status`/`node_online`, убрать имена/счётчики из публичного ответа (или отдавать только с admin/loopback) | OPS |
| **SEC-26** | Minor | Документация/код | Комментарий в шлюзе утверждает, что `bash_exec` рекламируется с `readOnlyHint=true`, хотя в таблице аннотаций он `LOCAL_WRITE` (`readOnlyHint=false`). Расхождение опасно: на «readOnlyHint» клиент строит своё решение о подтверждении | `gateway.py:2649-2651` против `gateway.py:2982-3007` и `core/mcp_tools.py:3001-3008` | Синхронизировать комментарий и таблицу; закрепить тестом `tests/test_tool_annotations.py` (уже есть — расширить) | BE + DOC |
| **SEC-27** | Minor | Реестр/устойчивость | `load_registry` при ошибке парсинга возвращает `{}` → `/api/register` считает имя свободным и выдаёт новый токен, то есть битый файл (следствие неатомарной записи) превращается в подмену identity узла | `gateway.py:305-312`, `:825-829` | Fail-closed + бэкап + восстановление; тест «повреждённый registry → 503, не новая запись» | BE |

**Итого:** 4 Blocker, 10 Critical, 9 Major, 5 Minor (28 findings: SEC-01…28).

---

## 4. Пропущенные детали

Сверено с фактическим `docs/team/WBS.md` (111 деталей) и `docs/team/TEST_PLAN.md` (111 тестов) — они появились в 04:29–04:31, уже после начала ревью, и `tools/check_team_coverage.py` на них зелёный. Ниже — только **реальные пробелы**: чего в WBS нет либо что сформулировано так, что не закрывает найденный риск. Уже покрытое не дублируется — ссылка в колонке «Зависит от». ID `SEC-1xx` не пересекаются с существующими `SEC-01…08`.

| WBS | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| SEC-101 | Модель угроз Wave 1 как приложение к ADR-004: активы A1–A8, противники P1–P7, границы TB1–TB8, таблица «что видит шлюз/облако/локальная учётка» | Документ в репо, ссылки из ADR-004; каждая граница имеет владельца и тест; совпадает с §2 этого отчёта | ADR-004 |
| SEC-102 | Сессия at rest: в БД только `sha256(id)`, plaintext id — только в cookie; `revoked_at`, `rotated_from`; единый TTL (см. SEC-28) | T-SEC-104 зелёный: дамп `sessions` не даёт войти | AUTH-11, AUTH-13 |
| SEC-103 | Привязка кода claim к устройству: `pair_id` + nonce/отпечаток device token; `GET /v1/devices/pair/{code}` отдаёт credentials только предъявителю nonce; `device_claims.account_id/tenant_id/attempts` | T-SEC-109 зелёный: подсмотренный код не даёт credentials | CLM-03, CLM-06 |
| SEC-104 | **P0 до Sprint 1:** guard в `bash_exec`, `run_job`, `unshare`; allowlist имён инструментов в `call_tool`/`handle_tool_call`, включая нерекламируемый `_http_share` | T-SEC-105 зелёный на текущей ветке; MOD-02 не откладывается до Sprint 3 | MOD-02 |
| SEC-105 | Read-конфайнмент: `read_roots` + deny-list секретов (`.ssh`, `.aws`, `.gnupg`, `agent.env`, `*.pem`) для `read_file`/`list_dir`/`grep_search`/`glob_find`; честно описать, что даёт `agent`-режим | T-SEC-106 зелёный: read-only не читает `~/.ssh/id_rsa` и `agent.env` | MOD-07 |
| SEC-106 | Судьба `MESH_ADMIN_TOKEN` при `MESH_ACCOUNT=1`: отключение либо скоуп на тенант + срок + аудит; зафиксировать в ADR-004 | T-SEC-103 зелёный: admin-эндпоинты недоступны/ограничены | AUTH-01 |
| SEC-107 | Реестр: хеш `link_key`, 0600/DACL, атомарная запись + lock, fail-closed при повреждении, бэкап/восстановление | T-SEC-108 зелёный: grep реестра без `lk_`; повреждённый JSON → 503 | AGT-04 |
| SEC-108 | Доверенный client IP: правый элемент `X-Forwarded-For` от известного прокси / `X-Real-IP`, allowlist хопов, bounded store | T-SEC-117 зелёный: XFF-спуф не создаёт новый бакет | AUTH-16 |
| SEC-109 | Гигиена логов: `access_log off`/маскирование `token` для `/sse`, `/mcp`, `/ws`; `Referrer-Policy`; расширение `redact.py` (JWT, `__Host-*`, `lk_`/`ak_`, `code=`); CI-сканер | T-SEC-101 зелёный: логи за скриптованную сессию без секретов | SEC-02 |
| SEC-110 | Step-up (fresh-auth ≤10 мин) для claim/revoke/delete: `step_up_at` в сессии, аудит, лимит попыток | T-SEC-111: без step-up → `step_up_required`, просроченный → отказ | AUTH-18 |
| SEC-111 | Аудит tamper-evident: `prev_hash`, `actor_account_id`, `ip_hash`, `request_id`, append-only, покрытие revoke/rotate/mode-change/`claim_failed` | T-SEC-102 зелёный: подмена/удаление события обнаруживается | CLM-09 |
| SEC-112 | БД: `PRAGMA foreign_keys=ON` в каждом соединении, `code_hash` в `device_claims`, WAL, 0600 | T-SEC-112: каскад удаления аккаунта работает; plaintext `code` отсутствует | AUTH-18 |
| SEC-113 | Ужесточение локального MCP-сервера узла (`core/server.py`): обязательный токен, `Origin`/`Host` allowlist, JSON-only, без `ACAO: *` | T-SEC-107 зелёный: cross-site POST с внешнего origin не выполняется | MOD-05 |
| SEC-114 | Privacy-страница = факт: что видит шлюз (usage, `ip_hash`, UA, `node_name`), чего не видит (args, пути, вывод); сверка с БД и перехваченным трафиком | T-SEC-113, подпись SEC | CAB-11 |
| SEC-115 | Pairing UX: маршрут `/pair` в кабинете или удаление `pair_url`; approve только от аккаунта, не от админ-токена | T-SEC-114: сценарий «код с экрана → кабинет» без админ-токена | CLM-10 |
| SEC-116 | CSV/formula-injection и clamp полей журнала (`agent_label`, `tool`): charset, ≤64, префикс `'` в CSV, экранирование в HTML | T-SEC-118 зелёный | USE-09 |
| SEC-117 | Порядок зависимостей: MOD-01…08 — не позже CAB-07/AGT-09, иначе переключатель режима в кабинете опережает принуждение | В `OWNERSHIP_MATRIX` у CAB-07/AGT-09 нет зависимости от невыполненного MOD; T-SEC-115, gate G1 | MOD-01, CAB-07 |

Порядок работ: SEC-104 (фикс `MESH_READ_ONLY`) — до Sprint 1. Иначе критерий MOD-02 не выполняется пять недель, а CAB-07/AGT-09 (Sprint 2) включают переключатель режима раньше, чем появляется принуждение (SEC-117).

---

## 5. Пропущенные тесты

ID — трёхзначные в домене соответствующего WBS (`T-<DOMAIN>-1xx`), чтобы не пересекаться с существующими `T-AUTH-01…18`/`T-SEC-01…08` и проходить `tools/check_team_coverage.py` (домен теста = домен WBS). `Blocking=да` — тест обязателен для релиза; ручные помечены в той же колонке.

| Test ID | WBS | Level | Steps | Expected | Automation | Env | Blocking |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| T-AUTH-101 | AUTH-05 | unit | Подписать ID token `alg=none`, затем HS256 публичным ключом Google, затем RS256 чужим ключом; отправить в callback | Все три → 400/401; сессия не создана; в логе только код ошибки, без токена | pytest | fake_oidc | да |
| T-AUTH-102 | AUTH-05 | integration | Фейковый IdP: новый `kid`; 100 запросов с несуществующим `kid`; выключить JWKS-эндпоинт | Новый `kid` → ровно один refetch; флуд → ≤N refresh/мин без отказа валидным; JWKS недоступен → fail-closed | pytest | fake_oidc | да |
| T-AUTH-103 | AUTH-07 | integration | (a) повторить callback-URL; (b) `state` из браузера A в браузере B; (c) callback без `nonce`; (d) callback через 11 мин | Все → 400, сессии нет, `login_failed` в аудите, state инвалидирован | pytest | test client | да |
| T-AUTH-104 | AUTH-03 | unit+e2e | `next` = `//evil.com`, `/\evil.com`, `https://evil`, `%2f%2fevil`, `..%2f..`, `javascript:alert(1)`, `\evil`, а также `next` из подделанной cookie | Все → 302 на `/account/`; кода/токена в `Location` нет; `next` берётся только из серверного state | pytest + Playwright | test client | да |
| T-AUTH-105 | AUTH-11 | integration | Cookie до логина; вход; старый id; logout; подделка подписи; 2 параллельные сессии; revoke-all; атрибуты `__Host-`/Secure/HttpOnly/SameSite/Max-Age; state-changing GET | После входа id новый; старый мёртв; logout убивает строку; подделка → 401; параллельные живы до revoke-all; атрибуты полные; GET ничего не меняет | pytest | test client | да |
| T-SEC-104 | SEC-102 | integration | Дамп `sessions`; вход с id из дампа; проверка idle/absolute TTL и `revoked_at` | В БД хеш, войти нельзя; TTL применяются; revoke-all ставит `revoked_at` | pytest | test client | да |
| T-AUTH-107 | AUTH-17 | unit | `MESH_AUTH_MODE=dev` + прод-`MESH_ACCOUNT_BASE_URL`: старт; затем `X-Forwarded-For: 127.0.0.1` на проде | Старт аварийно завершается; dev-вход не срабатывает по поддельному XFF | pytest | env | да |
| T-CLM-101 | CLM-04 | security | 1000 неверных кодов с одного аккаунта; затем 1000 с рандомным `X-Forwarded-For`; замер бакетов | 429 после N; одинаковое тело (нет оракула существования кода); `claim_failed` на каждую попытку; карта бакетов ограничена | pytest + ZAP | stage | да |
| T-CLM-102 | CLM-08 | integration | B вводит код устройства A; повтор того же кода; claim через 601 с; `device_id` чужого тенанта | B → 404 без деталей; повтор → 400/409; TTL → 400; чужой `device_id` → 404 | pytest | 2 аккаунта | да |
| T-SEC-109 | SEC-103 | integration | Сторонний клиент, знающий код (подсмотрен), дёргает `GET /v1/devices/pair/{code}` после approve; затем легитимное устройство | Сторонний credentials не получает (нужен nonce/device-token); устройство получает; второго ключа в реестре нет | pytest | 2 клиента | да |
| T-CLM-104 | CLM-07 | integration | Claim кода для уже привязанного узла; grep реестра на `*-2`; сравнить ключи до/после | Идемпотентно или 409; `*-2` нет; ключи узла не изменились | pytest | 2 аккаунта | да |
| T-MOD-101 | MOD-03 | contract | Агент `read_only` + `files:read`: `tools/list`; затем `tools/call` для `bash_exec`, `run_job`, `write_file`, `edit_file`, `share_file`, `serve_dir`, `device_capture`, `mesh_update`, `unshare` | Мутирующих нет в списке; все вызовы `mode_denied`; аргументов в тексте ошибки нет; журнал `status=denied` | pytest | gateway+node | да |
| T-SEC-105 | SEC-104 | integration | Прямой JSON-RPC на `127.0.0.1:8096/mcp`: `bash_exec`, `run_job`, `unshare`, `_http_share` при `MESH_READ_ONLY=1` и при `mode=read_only` | Отказ на обоих уровнях и для внутреннего `_http_share`; файл-маркер не создан, `job_list` пуст | pytest | node | да |
| T-SEC-106 | SEC-105 | unit | `read_file("~/.ssh/id_rsa")`, `read_file("agent.env")`, `list_dir("~")`, `grep_search` по домашнему каталогу в read-only | Отказ по deny-list/read roots; разрешённое чтение в workspace работает | pytest | node | да |
| T-MOD-104 | MOD-06 | integration | Узел 0.4.x без `ctx` / `hello` с чужой версией: мутирующие вызовы; статус в кабинете | `policy_unavailable`; устройство `degraded`; понижение режима применимо | pytest | old-node fixture | да |
| T-CTL-101 | CTL-10 | integration+manual | Control API: без токена, чужой токен, `Host: evil.com`, `Origin: https://evil`, `Content-Type: text/plain` + `PATCH /v1/settings`; `DELETE /v1/usage`; `icacls control.token` под второй учёткой Windows | Все враждебные → 401/403, настройки не изменились; вторая учётка токен не читает | pytest + manual | Windows | да |
| T-SEC-107 | SEC-113 | security | Playwright с `https://evil`: `fetch('http://127.0.0.1:8096/mcp',{method:'POST',headers:{'Content-Type':'text/plain'},body})`; отдельно запрос с `Origin: https://evil` | Запрос отклонён; `ACAO: *` на мутирующих ответах нет; без токена → 401 | Playwright | Win/mac/Linux | да |
| T-USE-101 | USE-11 | unit+e2e | `bash_exec("cat secret")`, `read_file("~/.ssh/id_rsa")`, ошибка с путём; `agent_label` = `=cmd\|' /c calc'!A0`, `<img src=x onerror=alert(1)>`, 10 000 символов, перевод строки | В журнале только allowlist-поля, ни args, ни путей; label усечён и экранирован; CSV-ячейка с `'`; HTML экранирован; ровно одна строка JSONL | pytest + Playwright | node+cabinet | да |
| T-USE-102 | USE-04 | integration+perf | Ротация на 10 МБ, чтение `.gz`, ретенция 90 дней, битая строка, 8 параллельных писателей, права/DACL | Старое читается, ретенция удаляет, битая строка игнорируется, строки не склеиваются, файл 0600/DACL only-user | pytest | node | да |
| T-USE-103 | USE-14 | integration | Устройство A шлёт агрегаты с `device_id` B; повтор дня; 2^63-значения; без HMAC и с неверным HMAC | Чужой `device_id` → 403/404; повтор идемпотентен; значения clamp; неверный HMAC → 401 | pytest | 2 устройства | да |
| T-AGT-101 | AGT-05 | integration | Создать агента; grep БД/реестра на `lk_`/`ak_`/`rhk_`; `GET .../agents`; revoke → вызов; rotate → старый/новый; 20 параллельных create/revoke | Ключ показан один раз, at rest SHA-256, в списках ключей нет; revoke → 401 <5 с; старый 401; нет потерянных обновлений и «воскресших» ключей | pytest | node+gateway | да |
| T-SEC-108 | SEC-107 | integration | `ls -l`/`icacls` реестра; grep на `lk_`; `kill -9` во время записи и перезапуск; подсунуть битый JSON | 0600/DACL, `lk_` нет; после kill запись целая; битый JSON → 503, новая запись не создаётся | pytest + manual | Linux+Windows | да |
| T-SEC-101 | SEC-109 | security | Скриптованная сессия (логин, claim, 5 вызовов, rotate): скан `usage*.jsonl*`, лога шлюза, access-лога nginx, CSV-экспорта, Referer на `lk_`, `ak_`, `eyJ`, `__Host-rhm_session`, `code=` | 0 совпадений; `?token=` в логах замаскирован; Referer наружу не уходит | CI-скрипт | stage | да |
| T-SEC-102 | SEC-111 | integration | Вызвать login/claim/revoke/mode-change; удалить событие через API и прямым SQL; удалить аккаунт и проверить остатки в `devices`/`agents`/`sessions`/`usage_daily` | API удаления нет; цепочка `prev_hash` ломается и обнаруживается; каскад удалил данные, в аудите tombstone; plaintext `code` отсутствует | pytest | node+gateway | да |
| T-SEC-103 | SEC-106 | integration | `MESH_ACCOUNT=1`: `approve`, `rotate`, `rotate-token`, `revoke` с `x-admin-token`; затем те же действия от аккаунта без step-up и со step-up старше 10 мин | Admin-эндпоинты отключены/скоуплены; без step-up → `step_up_required`; просроченный → отказ; 5 ошибок → блокировка и аудит | pytest | stage | да |
| T-SEC-118 | SEC-116 | unit+e2e | `agent_label` = `=cmd\|' /c calc'!A0`, `<img src=x onerror=alert(1)>`, 10 000 символов, перевод строки; экспорт CSV | CSV-ячейка с префиксом `'`, HTML экранирован, label усечён до 64, в журнале ровно одна строка | pytest + Playwright | node+cabinet | да |

**Дополнительные тесты — закрывают строки, которым не хватило места в первой таблице (T-SEC-111/112 — авто, остальные ручные):**

| Test ID | WBS | Level | Steps | Expected | Automation | Env | Blocking |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| T-SEC-110 | SEC-101 | manual | Ревью модели угроз: C/A/OPS сверяют §2 этого отчёта с кодом и конфигом; каждая граница TB1–TB8 получает владельца | Все границы имеют владельца и хотя бы одну проверку; расхождений с кодом нет; подпись C в `REVIEW_LOG.md` | manual checklist | — | да |
| T-SEC-117 | SEC-108 | manual+script | На живом nginx: запрос с `?token=...` и просмотр access-лога; `curl -H "X-Forwarded-For: 1.2.3.4"` напрямую в шлюз | `token` в логе замаскирован; прямой XFF не создаёт новый бакет | shell + grep | stage | да |
| T-SEC-111 | SEC-110 | security | Claim/revoke/delete без step-up; со step-up старше 10 мин; после успешного step-up; 5 ошибок подряд | `step_up_required`; просроченный → отказ; успешный проходит; блокировка и аудит | pytest | 2 аккаунта | да |
| T-SEC-112 | SEC-112 | integration | Удалить аккаунт и проверить `devices`/`agents`/`sessions`/`usage_daily`; `SELECT code FROM device_claims`; `PRAGMA foreign_keys` | Каскад сработал; `code` — хеш; pragma включена в каждом соединении | pytest | test db | да |
| T-SEC-113 | SEC-114 | manual | Сверить privacy-страницу с перехваченным трафиком и дампом БД | Обещанного «нет» действительно нет; подпись SEC | manual | stage | да |
| T-SEC-114 | SEC-115 | manual | Сценарий «код с экрана → кабинет» без админ-токена; проверить, что `/pair` не 404 и не требует `MESH_ADMIN_TOKEN` | Сценарий проходит от аккаунта; админ-токен не используется | manual | Windows | да |
| T-SEC-115 | SEC-117 | manual | Сверить порядок в `OWNERSHIP_MATRIX.md`: MOD-01…08 не позже CAB-07/AGT-09 | Нарушений нет; ruling записан | manual | — | да |

**Итого тестов:** 28 автоматизируемых (первые две таблицы: 25 обязательных + T-SEC-111, T-SEC-112, T-SEC-118 — для полноты матрицы) и 5 ручных/процессных (T-SEC-110, 113, 114, 115, 117). Каждая строка `SEC-101…117` получает минимум один тест — это проверено скриптом: у 17 строк ровно 17 покрытий, домены теста и WBS совпадают. Остальное из §3 покрывают существующие тесты `TEST_PLAN.md` (T-SEC-01…08: fixation, утечки, SQLi, XSS/CSP, CSRF, IDOR, лимиты; T-AUTH-16/17 — lockout и dev) — дублировать не нужно. **Только на железе, без автоматизации:** DACL под второй учёткой Windows; поведение браузера с `__Host-` на реальном домене; ротация ключей Google; подсмотр кода на экране.

---

## 6. Гейты перед релизом

**G0 (процесс).** Артефакты существуют, `tools/check_team_coverage.py` зелёный — проверено 11.11 в 04:36: `WBS items: 111 / ownership rows: 111 / tests: 111`, «Coverage complete». Перед G1 строки SEC-101…117 обязаны быть внесены в `WBS.md`, `OWNERSHIP_MATRIX.md` и `TEST_PLAN.md`, иначе они снова «ничьи».

| Гейт | Что должно быть зелёным | Доказательство |
| :--- | :--- | :--- |
| **G1. Read-only непреодолим** | T-MOD-101, T-SEC-105, T-SEC-106, T-MOD-104, T-USE-101 | Свежий вывод pytest на шлюзе и на узле + ручной вызов `127.0.0.1:8096` с записью экрана |
| **G2. Claim нельзя присвоить** | T-CLM-101…102, T-CLM-104, T-SEC-109, T-AUTH-105, T-SEC-103 | Два реальных аккаунта, два устройства; аудит с `claim_failed`; в реестре нет `*-2`; admin-токен не единственный путь |
| **G3. Секреты не лежат и не утекают** | T-AGT-101, T-SEC-108, T-SEC-101, T-USE-102, T-USE-103 | Grep по БД/реестру/логам/CSV (0 совпадений), `icacls`/`ls -l`, HMAC-ingest |
| **G4. OIDC/сессия/CSRF по conformance** | T-AUTH-101…105, T-AUTH-107, T-CTL-101 | Прогон против `tests/fake_oidc.py` + 30-минутный независимый пентест логина и claim с письменным результатом (C/SEC) |
| **G5. Локальная поверхность закрыта** | T-SEC-107, T-CTL-101, T-CLM-101 | Playwright с внешнего origin, вторая учётка Windows, nginx с маскированием `token`, XFF-спуф |

Релиз блокируется любым красным блокирующим тестом и любым открытым Blocker/Critical из §3.

---

## 7. Предложение по владению

| WBS-блок | Owner | Reviewer | Комментарий |
| :--- | :--- | :--- | :--- |
| SEC-101, SEC-114 (модель угроз, privacy) | SEC | C | Адversarial-проверка границ — второй ревьюер обязателен |
| SEC-102, AUTH-11…18 (сессии, CSRF, step-up) | SEC | API | Контракт cookie/CSRF затрагивает htmx |
| SEC-103, CLM-01…10 (claim) | BE | SEC | Owner не ревьюит сам |
| SEC-104, SEC-105, MOD-01…08 (политика, узел, read roots) | BE | SEC + C | Риск регресса Termux/device-ветки |
| SEC-106, SEC-107, AGT-03…08 (ключи, реестр, admin token) | BE | SEC | Обязательна проверка «ключа нет at rest» |
| SEC-108, SEC-109 (доверенный IP, логи) | OPS + BE | SEC | Нужен доступ к живому nginx |
| SEC-110, SEC-111, SEC-112 (step-up, аудит, БД) | BE | SEC | — |
| SEC-113, CTL-01…10 (локальные API) | DESK + BE | SEC + OPS | Второй ревьюер на Windows ACL и `core/server.py` |
| SEC-115, CAB-01…14 (pairing UX, кабинет) | UI | SEC | 404/403-семантика проверяется вручную |
| SEC-116, USE-09 (CSV/clamp) | BE | UI + SEC | — |
| SEC-117 (порядок MOD ↔ CAB) | P | SEC + QA | Решение — ruling в `REVIEW_LOG.md` |
| T-AUTH-101…105, T-AUTH-107, T-CLM-101…102, T-CLM-104, T-MOD-101, T-MOD-104, T-CTL-101, T-USE-101…103, T-AGT-101, T-SEC-101…112, T-SEC-113…115, T-SEC-117…118 | QA | SEC | QA владеет запуском, SEC — ожиданиями |

Второй ревьюер обязателен для: модели угроз (C), локальных API/DACL (DESK+SEC), ingest (SEC+PERF), порядка MOD↔CAB (QA). Владелец и ревьюер одной строки не совпадают — иначе строка не закрывается (устав §4).

---

## 8. Чек-лист готовности домена (что проверю лично перед подписью)

1. `pytest tests/test_account_auth.py tests/test_device_claim.py tests/test_session_context.py tests/test_usage.py tests/test_usage_ingest.py tests/test_control_api.py` — свежий вывод, red-green по каждому SEC-пункту приложен.
2. `python tools/check_team_coverage.py` → 0; в `OWNERSHIP_MATRIX.md` у каждого SEC-пункта есть Owner ≠ Reviewer.
3. Ручной прогон read-only: `tools/call` для девяти мутирующих инструментов через шлюз, напрямую в узел и с «старым» узлом — все три отказа.
4. Grep по `registry.json`, `account.db`, `usage*.jsonl*`, логам шлюза и nginx, CSV-экспорту на `lk_`, `rhk_`, `eyJ`, `__Host-rhm_session`, `code=` — 0 совпадений.
5. `icacls`/`ls -l` на `registry.json`, `account.db`, `usage.jsonl`, `control.token` под второй учётной записью Windows и под `nobody` на Linux.
6. IDOR-свип: для каждого `/account/api/*` подставить `device_id`/`agent_id`/`code` из второго аккаунта — ожидание 404 (не 403).
7. Playwright с `https://evil`: cross-site POST на `127.0.0.1:8096` и на control API; `Host: evil.com` — все отклонены.
8. Два реальных аккаунта: B пытается claim'ить код устройства A; A отзывает агента — вызовы падают < 5 с, у B ничего не меняется.
9. OIDC-негативы против фейкового IdP: `alg=none`, просроченный, чужой `aud`, `email_verified=false`, повтор callback, ротация JWKS.
10. Privacy-страница сверена с перехваченным трафиком и содержимым БД: обещанного «нет» (args, пути, вывод) действительно нет.

---

## 9. Что не проверено

- **Поведение Google OIDC** (реальная ротация ключей, `azp`, `email_verified` для workspace-аккаунтов) — только по документации, живого клиента нет.
- **Windows DACL/DPAPI** и поведение loopback для разных учёток — нужен реальный Windows-хост; выводы основаны на коде и общих свойствах ОС.
- **Реальный access-лог nginx** — выведен из конфига (`access_log` не отключён для `/sse`,`/mcp`), на живом сервере не смотрел.
- **Termux/Android-ветка** (`core/termux.py`, device-guards) — только по коду, без устройства.
- **Поведение Avalonia-трея** и `control.py` — реализации нет, ревью проектных требований.
- **Нагрузочные лимиты журнала** (10 МБ × 5, 90 дней, p95 сводки) — реализации нет; PERF должен подтвердить числа.
- **`registry.json` на боевом шлюзе** (права, владелец, бэкапы) — не проверено, конфиг деплоя не содержит `chmod`.
