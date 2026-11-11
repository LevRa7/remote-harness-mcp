# QA / Test Architect — ревью Wave 1

**Область:** тестовая стратегия, трассируемость, фикстуры, окружения, критерии прохождения, adversarial-набор, ручные проверки.
**Дата:** 2026-11-11.
**Прочитано:** `docs/team/REVIEW_BRIEF.md`, `docs/wave-1-auth-account-log-settings.md`, `docs/remote_harness_mcp_v2_plan.md` (§1.7, §6 E1–E5, §9), `docs/team/AGENT_TEAM.md`, `docs/team/WBS.md`, `docs/team/TEST_PLAN.md`, `docs/team/OWNERSHIP_MATRIX.md`, `docs/team/VERIFICATION_PROTOCOL.md`, `docs/team/REVIEW_LOG.md`, `pytest.ini`, `.github/workflows/ci.yml`, `tests/` (структура и представители), `gateway.py`, `core/mcp_tools.py`, `core/agent.py`, `install.ps1`, `tools/check_team_coverage.py`.
**Проверено запуском:**
- `pytest --collect-only -q` → **1258 тестов**; полный прогон → **1251 passed, 3 failed, 4 skipped, 114.7 s**. Три падения — `tests/test_rename_compat.py` (bash/WSL-окружение), к Wave 1 не относятся.
- `python tools/check_team_coverage.py --strict` → **exit 1 на двух прогонах**: первый — WBS 155, ownership 111, tests 111, 111/155, 44 `REV-*` без владельца; второй — WBS 167, ownership 158, tests 158, 158/167, 9 `REV-*` без владельца. Артефакты правились параллельно; вывод — гейт остаётся красным, а счётчик догоняет WBS.

> Артефакты `TEST_PLAN.md`, `OWNERSHIP_MATRIX.md` и `check_team_coverage.py` появились во время ревью (параллельные агенты). Отчёт оценивает их фактическое состояние на момент завершения, а не отсутствие.

---

## 1. Вердикт

**NOT_READY** по тестовой части. План продуктово полон, и за время ревью появился сильный бумажный контур: WBS 167 строк, TEST_PLAN 158 строк с конкретными шагами, матрица владельцев, чекер. Но **гейт трассировки красный на двух прогонах подряд** (44, затем 9 `REV-*` без владельцев), а чекер проверяет только согласованность markdown-таблиц — не запуск тестов, не JUnit и не доказательства. Три блокера: бумажная трассировка, отсутствие тестового каркаса/фикстур и неизмеримый coverage-гейт. После `REV-QA-01`, `REV-QA-02` и `REV-QA-07` можно начинать Sprint 1; `REV-QA-03` (chaos/миграции) и `REV-QA-06` (security-оракулы) обязательны до G4. Все строки blocking (сначала 110/111, затем 158/158) — это не приоритизация, а отсутствие приоритизации: красный любой строки останавливает всё.

**Blocker: 3. Critical: 2.**

---

## 2. Findings

| ID | Severity | Область | Проблема | Доказательство | Рекомендация | Владелец |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| QA-01 | **Blocker** | Трассируемость | Чекер красный на двух прогонах: 44, затем 9 `REV-*` без ownership/test; 111/155 → 158/167. Сам чекер — «бумажный»: парсит таблицы, но не вызывает pytest, не читает JUnit, не проверяет существование evidence-файлов. `T-*` не связан с pytest-node, поэтому 158 «тестов» могут существовать только в markdown. | `tools/check_team_coverage.py:9-16,121-139,195-199`; `--strict` → exit 1 дважды (111/155, 44 gaps; 158/167, 9 gaps); `TEST_PLAN.md` 158 строк, ни одной `nodeid`; `OWNERSHIP_MATRIX.md:8` evidence — строковый путь | `REV-QA-07`: связать `T-*`/`TS-*` с pytest через маркеры, проверять JUnit, свежесть evidence, покрыть все WBS-строки (включая `REV-*`) | QA + P |
| QA-02 | **Blocker** | Фикстуры/каркас | Sprint 0 обещает `tests/fake_oidc.py`, но в `tests/` нет `conftest.py`, фейкового IdP, фабрики приложения, инъекции часов, сетевого гарда. Тесты мутируют глобалы процесса: `gateway.active_sessions.clear()`, `monkeypatch REGISTRY_PATH`, `mcp_tools.configure(read_only=True)`. TEST_PLAN ссылается на env «local + fake OIDC», которого нет. | `wave-1:130`; отсутствуют `tests/fake_oidc.py`, `tests/conftest.py`, `requirements-gateway.txt`; `test_gateway_session_id.py:54,65,112`; `test_gateway_resilience.py:71-80`; `test_mcp_tools.py:199-200,275-277,594-599` | `REV-QA-01`: `conftest.py`, app factory, DI-часы, fake IdP, сетевой гард, per-test БД; запрет `.clear()` глобалов | QA + BE |
| QA-03 | **Blocker** | Coverage-гейт | DoD «покрытие нового кода ≥ 80%» нечем измерить: в CI не ставится `pytest-cov`, нет `coverage`-конфига, JUnit/coverage не публикуются; `pytest.ini` содержит только `pythonpath`. | `wave-1:549`; `plan:1369`; `ci.yml:58`; `pytest.ini:1-2`; `VERIFICATION_PROTOCOL.md:49-51` | `REV-QA-02`: `pytest-cov` branch, `diff-cover`, `--junitxml`, upload-artifact, отдельный job с зависимостями кабинета | OPS + QA |
| QA-04 | **Critical** | Adversarial-широта | Security-строк стало 15+ (`T-SEC-*`, `T-REV-SEC-*`), но нет fuzz/property-based, log/CSV-injection, проверки постоянного времени, IDOR на пагинации/cursor, XSS в разных контекстах, CSP-заголовков, replay-окна токенов. | `TEST_PLAN.md` T-SEC-01…08, T-REV-SEC-09…15; `REV-QA-06`; `WBS.md:146-157`; `wave-1:496-511` | `TS-SEC-*`, `TS-CAB-002`, `TS-USE-003`: корпуса OWASP, hypothesis, проверка заголовков и сравнений | SEC + QA |
| QA-05 | Major | Миграции/откат | 13 migration-строк и `T-REV-QA-03` описывают failpoint/`kill -9`/restore/up-down, но это остаётся текстом: исполняемого crash-теста нет, как нет проверки orphaned-записей `registry.json` после сбоя. | `TEST_PLAN.md` T-OPS-01…13, T-REV-QA-03; `WBS.md:163-164`; `wave-1:400-402`; `plan:402,1388` | `REV-QA-03`: failpoint после каждого DDL, `SQLITE_BUSY`/`ENOSPC`, up→crash→reopen→down→restore, diff схемы и контрольные суммы | BE + QA |
| QA-06 | **Critical** | Граница доверия ingest | T-USE-14 проверяет device token, replay и SQLi-литерал; T-REV-USE-16 добавляет `device_id` из auth и ownership агента, но cross-tenant-теста (устройство A пишет в tenant B) и проверки future/stale `day` нет. | `TEST_PLAN.md` T-USE-14, T-REV-USE-16; `WBS.md:100,188`; `wave-1:215,428-433` | `TS-USE-005`: cross-tenant, ownership агента, границы `day`, out-of-order, outbox | BE + SEC |
| QA-07 | Major | Control API | Host/Origin проверяются (T-CTL-10), но нет `Sec-Fetch-Site`, CORS-политики, токена в query, ACL named pipe, поведения при DNS rebinding с валидным Host и медленного SSE-клиента. | `TEST_PLAN.md` T-CTL-01…10; `WBS.md:106-115`; `wave-1:274-288` | `TS-CTL-001`: полный набор браузерных векторов + pipe ACL | SEC + DESK |
| QA-08 | Major | Режим/scopes | 8 MOD-строк детальны, но матрица «все инструменты × режим × scopes × слой» не параметризована; нет проверки `run_job`/`job_kill` как обхода, старого узла без `ctx` и расхождения `tools/list` с фактическим отказом. | `TEST_PLAN.md` T-MOD-01…08; `WBS.md:137-144`; `core/mcp_tools.py:3010-3020`; `test_mcp_tools.py:199,275,594` | `TS-MOD-001`: параметризация по `TOOL_ANNOTATIONS`, bridge/node/direct/legacy | QA + SEC |
| QA-09 | Major | Контракты API | Нет `contract`-уровня (0 строк) и OpenAPI/JSON-Schema для `/account/api/*`, `/v1/usage*`, control API: коды, конверт ошибок, идемпотентность, пагинация не зафиксированы машинно. | уровни TEST_PLAN: `contract 0`; `REV-CAB-15/16`; `TEST_PLAN.md` T-OPS-04 лишь compat-матрица | `REV-QA-08`: схемы + contract-тесты + негативные схемы | API + QA |
| QA-10 | Minor | a11y/i18n | a11y/i18n спрятаны в e2e-строки (`T-CAB-14`, `T-REV-UI-05/06`); нет отдельного уровня/axe-гейта, screen reader и контраста трея в CI. | `TEST_PLAN.md` T-CAB-14, T-REV-UI-05/06; `WBS.md:206-210` | `REV-QA-09`: axe-гейт, key-parity, TZ/locale-матрица, NVDA вручную | UI + QA |
| QA-11 | Minor | Производительность | 6 perf-строк появилось, но 5 из них — архитектурные (`REV-PERF-01…05`), единственная нагрузочная `REV-PERF-06` не имеет CI-джоба, сида и тренда p95. | `TEST_PLAN.md` T-REV-PERF-01…06; `REV-PERF-06`; `plan:1353` | `REV-QA-09`: `locust`/`k6`, `bench.json`, p95/p99, RSS, weekly trend | PERF + QA |
| QA-12 | Major | Chaos | Уровень `chaos` = 0; `REV-QA-03` требует failpoint/`kill -9`/`ENOSPC`/`SQLITE_BUSY`, но ни одной строки в TEST_PLAN нет. | уровни TEST_PLAN: `chaos 0`; `WBS.md:220`; `plan:1352` | `TS-CHAOS-001` + `REV-QA-03` | QA + BE |
| QA-13 | Major | Flaky/время | Политика flaky есть, но нет маркеров, таймаутов, ретраев, карантина, детектора. Все строки blocking (110/111 → 158/158) — приоритетов нет. Часть «тестов» — тавтология: `test_remote_execution_timeout_resilience` не вызывает продукт. | `VERIFICATION_PROTOCOL.md:97-101`; `TEST_PLAN.md` все blocking; `pytest.ini:1-2`; `test_resilience_and_perf.py:19-43`; локально 3 failed/4 skipped | `REV-QA-04`: `pytest-timeout`, `--strict-markers`, P0/P1, 3× blocking, карантин ≤ 10 дней | QA + OPS |
| QA-14 | Major | Windows/трей | 11 TRY-строк — manual, без headless-каркаса; автозапуск Installer (`Startup .vbs` + `schtasks`) и трей (`HKCU\Run`) не тестируются на сосуществование; CI собирает только EXE. | `TEST_PLAN.md` T-TRY-01…11; `wave-1:267`; `install.ps1:1060-1080,1135-1141`; `ci.yml:116-171`; `REV-UI-01` | `TS-TRAY-001` + Windows CI (HKCU/ACL/junction/DPI) | DESK + QA |
| QA-15 | Major | Устаревший baseline | План утверждает «434 теста», фактически 1258; нет зафиксированного baseline-отчёта и числа passed для CI. | `plan:120,591`; `pytest --collect-only` → 1258; локальный прогон 1251 passed | Заменить числа на CI-бейдж/артефакт baseline; хранить `baseline.json` | P + OPS |
| QA-16 | Major | Флаг/деградация | `MESH_ACCOUNT=0` покрыт T-OPS-03, но нет теста отсутствия `jinja2`/`PyJWT`/`httpx`: импорт не должен ронять MCP, `/account` — «не настроен». | `wave-1:111-114`; `TEST_PLAN.md` T-OPS-03; `requirements-gateway.txt` отсутствует | `TS-OPS-001`: import-blocker, `/account` 503/страница, MCP зелёный | BE + QA |
| QA-17 | Major | Удаление аккаунта | `T-REV-CLM-13` описывает unlink/удаление, но в схеме `ON DELETE CASCADE` есть только у `sessions` и `agents`; `devices`/`tenants`/`usage_daily` останутся сиротами с PII. Grace-период не тестируется. | `WBS.md:182`; `TEST_PLAN.md` T-REV-CLM-13; `wave-1:321,330,337,354` | Определить порядок удаления/анонимизации, FK, фоновую зачистку; `TS-AUTH-008` | BE + SEC |
| QA-18 | Major | Доказательства | `verification` требует артефакты, но чекер проверяет лишь непустую строку evidence; файл может не существовать, быть старым или чужим. Подпись в `REVIEW_LOG` — свободный текст, retention/upload в CI отсутствуют. | `check_team_coverage.py:174-175`; `VERIFICATION_PROTOCOL.md:9-22,118-133`; `REVIEW_LOG.md:42-46` | `REV-QA-05`: `evidence.json` (commit, cmd, exit, sha256, reviewer), проверка существования/свежести, upload 30/90 дней | QA + OPS |
| QA-19 | Minor | ID/blocking | Формат ID есть (`T-<AREA>-<NN>`), но нет pytest-node/маркеров, нет `TS-*` для кросс-WBS сценариев, все строки blocking обесценивают гейт. | `TEST_PLAN.md`; `check_team_coverage.py:41-44`; `WBS.md` 155 строк | Ввести `T-*` (конформанс) + `TS-*` (сценарии), P0/P1, маркеры | QA |
| QA-20 | Minor | Тестовые данные | Фейковый OIDC/ключи/токены не описаны; нет политики синтетических секретов. Хороший образец уже есть в `test_redact.py`. | `test_redact.py:6-14`; `ci.yml:179-189`; `wave-1:130` | Фиксированные fake-маркеры, gitleaks-allowlist только для `tests/fixtures`, скан артефактов | SEC + QA |

---

## 3. Пропущенные детали (с WBS-ID)

WBS уже содержит 44 строки `REV-*` (`WBS.md:174-227`), в том числе `REV-QA-01…06`. Их надо **связать** с тестами и владельцами, а не дублировать. Новые ID — только там, где дыра не закрыта.

| WBS-ID | Деталь | Почему без неё нельзя | Критерий приёмки |
| :-- | :-- | :-- | :-- |
| REV-QA-01 | Тестовый каркас: `conftest.py`, app factory, DI-часы, fake IdP, net guard, per-test БД | TEST_PLAN ссылается на «fake OIDC», которого нет | Ни одного модульного глобала в тестах; 2 воркера |
| REV-QA-02 | Coverage/CI-гейты: `pytest-cov` branch, `diff-cover`, JUnit, artifacts, job кабинета | 80% не измеряется | Coverage-отчёт в PR; пороги в G4/G5 |
| REV-QA-03 | Chaos/миграции: failpoint после DDL, `kill -9`, `SQLITE_BUSY`, `ENOSPC`, restore | Риск потери данных | 0 частичных данных; restore равен бэкапу |
| REV-QA-04 | Flaky: `pytest-timeout`, `--strict-markers`, P0/P1, 3× blocking, карантин ≤ 10 дней | Все строки blocking; время/стабильность не контролируются | 3× без вариаций; карантин-реестр |
| REV-QA-05 | Evidence: `evidence.json`, commit/cmd/exit/sha256, проверка файла, retention | Текущий чекер принимает строку как доказательство | Чекер валидирует файл, подпись, свежесть |
| REV-QA-06 | Security-оракулы: матрица «угроза → тест → точное ожидание», fuzz | 15+ строк не покрывают adversarial-широту | Каждая угроза §9 имеет кейс и порог |
| **REV-QA-07** | Traceability v2: `T-*` + `TS-*` → pytest-node/JUnit; покрытие всех WBS-строк | Текущий чекер красный и не запускает тесты | `--strict` = 0; 167/167; 0 orphan |
| **REV-QA-08** | OpenAPI/JSON-Schema и contract-тесты account/control/usage API | Нет уровня `contract` | Схемы в репо, негативные схемы, единый `error_code` |
| **REV-QA-09** | Матрица окружений и отдельные CI-джобы: py3.12, macOS, Playwright, старый узел, TZ, perf | CI — 3 комбинации, без браузеров и нагрузки | 6+ джобов; nightly; bench.json |
| **REV-QA-10** | Политика тестовых данных/секретов: синтетика, allowlist, скан артефактов | Риск утечки Google secret | gitleaks + скан artifacts/logs/JSONL |
| **REV-QA-11** | Набор `TS-*` (40 кросс-WBS сценариев): IDOR-матрица, race, отзыв во время вызова, log/CSV injection, миграционный crash | Один тест на WBS не ловит сценарии | TS-каталог в TEST_PLAN, каждый связан с ≥2 WBS |
| **REV-AUTH-21** | Ротация `MESH_SESSION_SECRET` (dual-key verify, окно) и dev-cookie `__Host-`/Secure на `http://localhost` | Смена секрета разлогинивает всех; dev/prod расходятся | ADR + тест двух ключей и dev-cookie |
| **REV-OPS-12** | Права `MESH_ACCOUNT_DB` и каталога (PII: email, `google_sub`, `ip_hash`), шифрование бэкапа | В §9 нет 0600/0700 | Проверка на старте + тест + runbook |
| **REV-SEC-16** | Security headers/CSP: HSTS, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors`, CSP report-only | XSS-тест без заголовков неполон | Заголовки на всех HTML; тест |
| **REV-USE-17** | Планировщик retention/purge и граница суток по часам узла | 90 дней и «день» агрегата не воспроизводимы | Инъецируемые часы, TZ-тест, идемпотентный purge |

**Отдельно:** `REV-UI-05/06/07`, `REV-PERF-02/06`, `REV-SEC-11/12/13` уже покрывают часть a11y, retention, read-confine и нагрузку — их достаточно связать с тестами, новые ID не нужны.

---

## 4. Пропущенные тесты: уровни и стратегия (Q1)

Коротко: **на бумаге 6 уровней, отсутствуют 4 + rollback частично.** TEST_PLAN — 158 строк: unit 57, integration 24, e2e 31, manual 12, security 15, migration 13, perf 6. Уровней `contract`, `chaos`, `a11y`, `i18n` нет ни одной строкой; a11y/i18n спрятаны в e2e-ряды, perf — в архитектурные REV-PERF-строки.

| Уровень | Состояние | Чего не хватает | WBS |
| :-- | :-- | :-- | :-- |
| Unit | 57 строк, хорошие шаги | Нет кода; нет hypothesis; account-модулей ещё нет | AUTH, CLM, USE |
| Contract | **0 строк** | OpenAPI/JSON-Schema, негативные схемы, `error_code` | CAB, CTL, USE |
| Integration | 24 строки | Нет БД/app factory/fake node; env «local gateway + tmp SQLite» не существует | CLM, AGT, CTL |
| E2E | 31 Playwright-строка | Нет Playwright-конфига, fake IdP, двух аккаунтов, trace/видео | CAB, AUTH |
| Security | 15 строк, местами с точными порогами | Нет fuzz, log/CSV injection, constant-time, IDOR-пагинации, CSP-заголовков | SEC |
| Chaos | **0 строк** | DB lock/corrupt, диск, kill -9, сеть, рестарт | OPS, USE |
| Perf | 6 строк: 5 архитектурных + 1 нагрузочная | Нет CI-джоба, сида, тренда p95/p99 и памяти | CAB, CTL, USE |
| A11y | **0 строк** (e2e-ряды CAB-14/REV-UI) | axe-гейт, клавиатура, контраст, screen reader | CAB, TRY |
| i18n | **0 строк** (e2e-ряды CAB-14/REV-UI) | Паритет ключей трея/кабинета, плюрализация, локали дат | CAB, TRY |
| Migration | 13 строк | Нет исполняемого crash/failpoint-теста | OPS |
| Rollback | Частично (T-OPS-01/13) | Нет restore-теста на чистую машину после сбоя | OPS |

**Вывод:** набор покрывает функциональные happy-path и часть security лучше, чем ожидалось от §10, но «каждая мелочь» требует contract/chaos/perf/a11y/i18n и сценарного слоя `TS-*`. Иначе 80% будет покрытием форм, а не поведения.

---

## 5. Q2. Трассировка «требование → WBS → тест → доказательство»

### 5.1. Что уже есть и почему этого мало

- `WBS.md` — 167 атомов (111 исходных + 56 `REV-*`).
- `TEST_PLAN.md` — 158 строк `T-<AREA>-<NN>`, по одной на WBS; шаги/ожидания местами очень конкретны (T-SEC-07/08, T-OPS-01, T-CLM-03, T-CAB-14, T-TRY-08).
- `OWNERSHIP_MATRIX.md` — 158 строк, evidence как строковый путь.
- `check_team_coverage.py` — проверяет: один ownership на WBS, owner≠reviewer, существование теста в TEST_PLAN, домен, blocking. **Не проверяет:** запуск pytest, JUnit, существование файла evidence, свежесть, `TS-*`.

**Факт:** `--strict` красный на двух прогонах (`111/155`, 44 gaps; `158/167`, 9 gaps). Значит, G5 не может быть пройден даже формально, а 158 «тестов» не доказывают, что существует хотя бы один исполняемый.

### 5.2. Целевая схема

Атом требования — строка WBS (167). Два вида тестов:

- `T-<AREA>-<NN>` — **конформанс 1:1 к WBS** (уже есть; 158 строк). Меняет только свою WBS.
- `TS-<AREA>-<NNN>` — **сценарий/атака**, покрывает несколько WBS (40 кейсов из §11). Не заменяет `T-*`, а добавляет межстрочные проверки.

Таблицы:

**(A) WBS → тесты** (генерируется, между маркерами):

```markdown
<!-- trace:begin id=wave1 -->
| WBS | Risk | Обязательные уровни | T-тест | TS-тесты | Blocking | Owner | Reviewer |
| :-- | :-- | :-- | :-- | :-- | :--: | :-- | :-- |
| AUTH-02 | high | unit, integration, security | T-AUTH-02 | TS-AUTH-001 | P0 | BE | SEC |
| CLM-03 | high | unit, integration, security | T-CLM-03 | TS-CLM-003 | P0 | BE | SEC |
| CAB-14 | medium | a11y, i18n, e2e | T-CAB-14 | TS-CAB-004 | P1 | UI | QA |
| REV-CLM-13 | high | integration, security | T-REV-CLM-13 | TS-AUTH-008 | P0 | BE | SEC |
<!-- trace:end -->
```

**(B) Тест → доказательство:**

```markdown
| Test ID | WBS | Level | Blocking | Node ID | Evidence | Status |
| :-- | :-- | :-- | :--: | :-- | :-- | :-- |
| T-AUTH-02 | AUTH-02 | unit | P0 | tests/test_account_auth.py::test_start_pkce_state | artifacts/AUTH-02/T-AUTH-02/ | planned |
| TS-CAB-001 | CAB-03, CAB-05, SEC-06 | security | P0 | tests/test_account_api.py::test_idor_matrix | artifacts/SEC-06/TS-CAB-001/ | planned |
```

**(C) `evidence.json`:**

```json
{
  "test_id": "TS-CAB-001",
  "wbs": ["CAB-03", "CAB-05", "SEC-06"],
  "commit": "9f3c2ab",
  "utc": "2026-11-11T04:30:00Z",
  "cmd": "pytest -q -m 'test_id and security'",
  "exit": 0,
  "junit": "junit.xml",
  "coverage": "coverage.xml",
  "artifacts": ["stdout.log", "findings.json"],
  "owner": "SEC",
  "reviewer": "QA",
  "verdict": "passed"
}
```

### 5.3. Расширение чекера (минимальное, не переписывание)

Добавить к существующему `check_team_coverage.py`:

1. `pytest --collect-only -q` → множество nodeid; каждый `T-*`/`TS-*` из TEST_PLAN обязан иметь `nodeid` (новая колонка) и быть в множестве.
2. `pytest --junitxml=artifacts/junit.xml` → каждый blocking-тест имеет `testcase` без `failure/error/skipped`; параметры — все.
3. `--strict` для `REV-*`: каждая из 167 строк имеет owner, reviewer, тест (T или TS); текущие 9 gaps → fail.
4. `evidence.json` существует, `commit == HEAD`, тестовый файл не менялся после прогона (`git diff --quiet <commit> -- <nodeid-file>`).
5. Manual: `manual.md` с обязательными полями, скриншоты существуют, `reviewer != owner`, подпись в `REVIEW_LOG`.
6. Orphans: каждый собранный тест с маркером `test_id` есть в матрице; каждый `TS-*` ссылается на ≥2 WBS.
7. `T-*` regex: `\d{2,3}`; `TS-*`: `\d{3}`; запрет переиспользования (retired → отдельный статус).
8. `--json` отчёт для CI; exit 2 — инфраструктура (collection/JUnit).

### 5.4. Скелет (патч к существующему чекеру)

```python
# добавить в tools/check_team_coverage.py
import json, subprocess, xml.etree.ElementTree as ET

TEST_ID = re.compile(r"^T-[A-Z]+-\d{2,3}$")
SCENARIO_ID = re.compile(r"^TS-[A-Z]+-\d{3}$")

def collected() -> set[str]:
    p = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                        "-p", "no:cacheprovider"], cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"collection failed:\n{p.stderr}")
    return {ln.split("::")[0] + "::" + ln.split("::")[1].split("[")[0]
            for ln in p.stdout.splitlines() if "::" in ln}

def junit_cases(path: Path) -> dict[str, ET.Element]:
    out = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        cls = tc.get("classname", "").replace(".", "/") + ".py"
        out[f"{cls}::{tc.get('name', '').split('[')[0]}"] = tc
    return out

# в main(), после load_tests():
# dataclass Test расширяется полями nodeid: str и evidence: Path;
# в TEST_PLAN добавляются колонки Node ID и Evidence.
nodes = collected()
cases = junit_cases(ROOT / "artifacts/junit.xml")
for tid, test in tests.items():
    if not (TEST_ID.match(tid) or SCENARIO_ID.match(tid)):
        problems.append(f"{tid}: bad id format")
    if test.nodeid not in nodes:
        problems.append(f"{tid}: node not collected: {test.nodeid}")
        continue
    tc = cases.get(test.nodeid)
    if tc is None:
        problems.append(f"{tid}: no JUnit result")
    elif tc.find("failure") is not None or tc.find("error") is not None:
        problems.append(f"{tid}: failed")
    elif test.blocking and tc.find("skipped") is not None:
        problems.append(f"{tid}: blocking skipped")
    ev = ROOT / test.evidence / "evidence.json"
    if not ev.is_file():
        problems.append(f"{tid}: no evidence.json")
```

### 5.5. CI-джоб

```yaml
traceability:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
      with: { fetch-depth: 0 }
    - run: pip install -r requirements-gateway.txt pytest pytest-cov pyyaml
    - run: pytest -q --junitxml=artifacts/junit.xml --cov=account --cov=core \
             --cov-branch --cov-report=xml --cov-fail-under=80
    - run: python tools/check_team_coverage.py --strict --junit artifacts/junit.xml
    - uses: actions/upload-artifact@v4
      with: { name: evidence, path: artifacts/, retention-days: 30 }
```

Таблицы не редактируются руками: PR меняет `traceability.yaml` или TEST_PLAN, генератор (`tools/build_team_matrix.py`) перестраивает markdown, чекер запускает тесты. Иначе «машинно» снова разойдётся с «человечески».

---

## 6. Q3. Фикстуры и изоляция

TEST_PLAN уже называет окружения (`local + fake OIDC`, `browser + seeded DB`, `staging VM`), но **фикстур нет**. Нужны:

| Фикстура | Scope | Что даёт | Изоляция |
| :-- | :-- | :-- | :-- |
| `fake_oidc` | session-ключ, function-состояние | RSA-ключ, JWKS, `/authorize`, `/token`, ID token с настраиваемыми `exp/iat/aud/azp/nonce/email_verified`; режимы `bad_sig`, `alg_none`, `unknown_kid`, `jwks_500`, `rotate` | Никакой сети; ключ один раз в сессии, коды/нонсы per-test; `kid` детерминирован |
| `account_app` | function | Starlette-приложение `create_account_app(config, db, clock, oidc)` | App factory; env через `monkeypatch`; без импортных глобалов |
| `db` | function | SQLite-файл в `tmp_path`, миграции до head, `foreign_keys=ON`, WAL | Свежая БД на тест; teardown закрывает соединения; `:memory:` — только для чистых репозиториев |
| `fake_gateway` | function | ASGI-заглушка `/v1/devices/pair`, `/account/api/claim`, `/v1/state` | Не импортирует 3450-строчный `gateway.py`; состояние — объект, не глобал |
| `fake_node` | function | WS-сервер с `hello` v1/v2, `initialize`, `tools/call`, `pause`, `revoke`, `slow`, `crash`; запись кадров | Эфемерный порт; «прямой вызов» — tool-функция с `SessionContext` без сокета |
| `sse_client` | function | `httpx.AsyncClient(transport=ASGITransport(app))`, парсер SSE, ожидание предиката, реконнект | Без реального сокета и `sleep`-поллинга; heartbeat — виртуальные часы |
| `clock` | function | `FakeClock(now)`, `advance(seconds)`; `Clock`-протокол в `account`, `core/usage`, claim/session | Никаких `time.time()`/`datetime.now()` в тестируемом коде; boundary TTL |
| `fs` | function | `tmp_path` + `MESH_CONFIG_DIR`; `chmod`, ACL-хелпер, маркеры `posix_perms`/`windows_acl` | 0600 на POSIX; ACL на Windows, не «skip» |
| `net_guard` | autouse, session | Блокирует `socket.connect` вне loopback; ASGI/loopback разрешены | Забытый внешний URL падает с понятной ошибкой, а не висит |
| `ids` | function | Детерминированные `call_id`, `session_id`, `agent_id`, ключи; режим коллизии | Не патчит `uuid` глобально; генератор инъектируется |
| `secrets` | function | Синтетические маркеры (`ghp_`, `GOCSPX-`, `Bearer`), сканер логов/JSONL/CSV | Маркеры уникальны; проверка в teardown всех артефактов |

**Правила изоляции (в `conftest.py`):**

1. **Никаких глобальных мутаций.** Пока legacy-`gateway.py` не переписан, fixture снимает снимок `active_tunnels`, `active_sessions`, `active_sse_*`, `pending_pairs`, `REGISTRY_PATH` и восстанавливает в `finally`; тесты не вызывают `.clear()`.
2. **Одна БД на тест.** Общий `/var/lib/rhm/account.db` запрещён; путь только из `tmp_path`; проверка «БД не создалась в рабочем каталоге» в teardown.
3. **Время только через `Clock`.** TTL — `advance(−1/0/+1)`, не `sleep`; тест «в коде нет `time.time()`» патчит его на `raise`.
4. **Сеть только in-process.** Fake IdP, ASGI, loopback; `net_guard` autouse; никаких `requests` к Google.
5. **Случайность инъектируется.** Коллизии `state`/кодов проверяются детерминированно.
6. **Порты/имена эфемерные.** `port=0`, уникальный pipe; сокетные тесты — `serial`, не под xdist.
7. **Chaos — в подпроцессе.** `kill -9`, `ENOSPC`, `SQLITE_BUSY` — отдельный процесс со своим `tmp_path`; родитель проверяет exit и отсутствие частичных файлов.
8. **Teardown строгий.** `-W error::ResourceWarning`, проверка незакрытых соединений/потоков; утечка fd — падение теста.

**Про SQLite in-memory:** для unit-тестов репозиториев допустим `:memory:`, но для API/миграций/бэкапа/локов — только файл в `tmp_path`. In-memory даёт разные БД на каждое соединение (без shared-cache), не поддерживает нормальный WAL и не позволяет проверить crash/restore; «in-memory» здесь создаёт ложную уверенность.

---

## 7. Q4. Матрица окружений

| Ось | В CI (авто) | На стенде (ручно) | Что проверяется |
| :-- | :-- | :-- | :-- |
| Linux | Ubuntu 22.04/24.04, Python 3.11/3.13 (добавить 3.12), full suite + account | — | SQLite-версии, POSIX 0600, symlink, locale |
| Windows | `windows-latest`, Python 3.13, full suite + PowerShell | Windows 11 + обычный пользователь | HKCU Run, `icacls`, junction, long path, UAC, SmartScreen |
| macOS | nightly: macOS 14 arm64, build + unit | macOS 14/15 + Safari | Трей — Wave 2; сборка/пути, чтобы не сгнить |
| Браузеры | Playwright: Chromium, Firefox, WebKit на Linux | Safari (macOS), Edge, мобильный Chrome/Safari | Логин-редиректы, cookie, viewport, CSP |
| Python | 3.11 (floor), 3.13 (current); 3.12 — добавить | — | Синтаксис/зависимости; SQLite |
| Старые узлы | Предыдущий релиз 0.4.8: payload/venv, protocol v1 `hello`, legacy `link_key` | Реальный узел 0.4.x | T-OPS-04, `tools/list`, link-key, агент `default` |
| Сеть | `net_guard`; nightly toxiproxy (latency/drop/DNS) | Captive portal, Wi-Fi flap, VPN, offline | Outbox, реконнект SSE, `node_paused` |
| DPI/экран | Avalonia headless: scale-factor layout unit | 100/125/150/200%, два монитора | Обрезка меню, иконка, попапы, координаты |
| Локаль/TZ | `TZ=UTC` + `TZ=Asia/Kolkata`, `LANG=ru_RU`/`en_US` | Реальная локаль ОС | Даты, числа, плюрализация, «день» агрегата |
| Железо/Termux | Существующие fixtures; не Wave 1 | Android/Termux при необходимости | Не блокирует Wave 1 |

**Реально автоматизировать:** account/API, SQLite, fake IdP, браузерные сценарии кабинета, a11y (axe), i18n-ключи, миграции, chaos в Linux-контейнере, контрольные суммы артефактов, Windows registry/ACL на runner (осторожно: runner — админ).
**Только стенд:** реальный Google, ребут и автозапуск, DPI/мультимонитор, SmartScreen/антивирус, screen reader, мобильный Safari, captive portal, потеря питания, апгрейд с 0.4.8 на реальных данных.

---

## 8. Q5. Критерии прохождения

| Метрика | Порог | Инструмент | Гейт |
| :-- | :-- | :-- | :-- |
| Line coverage нового/изменённого кода | ≥ 80% | `pytest-cov --cov-branch`, `diff-cover` | PR + G4 |
| Branch coverage | ≥ 70%; critical (`auth`, `claim`, `policy`, `usage`) ≥ 85% | `coverage.py` | G4 |
| Critical-модули, line | ≥ 90% | `coverage.py` | G5 |
| Mutation score | ≥ 70% на `auth/claim/policy`; выжившие триажированы | `mutmut` (nightly) | G5 |
| Flaky | 0 вариаций на 3× blocking; flake-rate < 1% | 3 job/`pytest-repeat` | постоянно |
| Время | PR ≤ 12 мин; nightly ≤ 45 мин; один тест ≤ 120 с | `--durations`, `pytest-timeout` | PR |
| Артефакты | JUnit, `coverage.xml`, Playwright trace/видео при падении, логи, `evidence.json` | CI upload | каждый прогон |
| Ручные | Все `M-*`/manual-строки подписаны, скриншоты открываются, `reviewer != owner` | чекер | G5 |

**Flaky-политика (развитие `VERIFICATION_PROTOCOL.md:97-101`):**

- Unit/integration — **retries = 0**. E2E — максимум 1 ретрай, только с маркером `flaky`, issue и владельцем.
- Новый blocking-тест прогоняется 3× в PR; нестабильный — не мержится.
- Карантин: маркер `quarantine`, запись в `REVIEW_LOG`, срок ≤ 10 дней, blocking карантинить нельзя.
- Запрещены `sleep > 100 ms`, внешняя сеть, зависимость от порядка; «иногда зелёный» тест удаляется или переписывается.
- `pytest.ini`: `--strict-markers`, `--strict-config`, `timeout=120`, маркеры `wbs/test_id/level/blocking/manual/chaos/perf/a11y/i18n/windows/serial/flaky/quarantine`.
- Приоритеты: P0 (security/данные/релиз), P1 (UX/деградация), P2 (качество). Сейчас все строки blocking (158/158) — это P0-инфляция.

**Проход релиза:** 0 failed/errors, 0 неожиданных skip у blocking, coverage-пороги, mutation-порог, perf-бюджеты, чекер = 0, все Critical закрыты, ручные подписаны. Красный P0 — стоп G5.

---

## 9. Q6. Negative / adversarial

| # | Атака/сбой | Тест | Оракул |
| :-- | :-- | :-- | :-- |
| 1 | `state` отсутствует/пуст/чужой/повторён/из другой сессии | TS-AUTH-001 | 400/403, сессия не создана, аудит |
| 2 | `nonce` отсутствует/не совпал/повторён | TS-AUTH-001 | отказ до upsert |
| 3 | `alg=none`, HS256 публичным ключом, неизвестный `kid` | TS-AUTH-002 | отказ; ровно один refresh JWKS; fail-closed при 500/timeout |
| 4 | Ротация ключа, старый `kid`, JWKS-кэш | TS-AUTH-002 | старый токен по старому ключу; новый — после refresh |
| 5 | `exp`/`iat` ±60/±61 с, чужой `aud`/`azp`/`iss` | TS-AUTH-003 | на границе — допуск ≤60 с; иначе отказ |
| 6 | `email_verified=false`, email с регистром/пробелами/дубль | TS-AUTH-003 | отказ; нормализация; нет дубля аккаунта |
| 7 | `next`: `//evil`, `https://evil`, `/\evil`, `%2f%2fevil` | TS-AUTH-004 | только локальный allowlist |
| 8 | Фиксация сессии, подмена cookie, logout, параллельные сессии | TS-AUTH-005 | новый `session_id` после логина; старый невалиден |
| 9 | CSRF: нет/пусто/чужой токен, `Origin: null`, cross-site | TS-AUTH-006 | 403; GET не мутирует |
| 10 | Перебор логина, user enumeration | TS-AUTH-007 | 429 + `Retry-After`; одинаковый ответ для несуществующего email |
| 11 | Перебор claim-кода, распределённые IP | TS-CLM-004 | лимит по IP+аккаунту; валидные заявки не страдают |
| 12 | Два параллельных claim одного кода | TS-CLM-003 | ровно один успех, один 409/400, одна привязка |
| 13 | Чужой tenant/аккаунт, уже привязанное устройство | TS-CLM-005 | 404/400 без деталей; аудит |
| 14 | IDOR по всем ID и cursor-пагинации | TS-CAB-001 | 404 идентичен «не существует»; данные B не изменены |
| 15 | XSS в имени/метке/workspace/ошибке; SQLi в фильтрах/сортировке | TS-CAB-002 | экранирование, CSP без inline, параметризация |
| 16 | CSV-формула `=cmd|...`, log injection `\n{...}`, NUL | TS-USE-003/004 | CSV-экранирование, JSONL остаётся валидным |
| 17 | `ingest` с чужим `device_id`/`tenant_id`, replay, ±день | TS-USE-005 | канонический device из токена; идемпотентность; 0 cross-tenant |
| 18 | Отзыв агента во время вызова; понижение режима на лету | TS-AGT-004, TS-MOD-001 | политика in-flight; следующий вызов 401/`mode_denied` |
| 19 | Workspace: `..`, symlink, junction, UNC, длинный путь, удалённая папка | TS-MOD-002 | fail-closed; `workspace_unavailable`; cwd внутри |
| 20 | Повреждённый журнал, `ENOSPC`, `kill -9`, DB locked/corrupt, сеть | TS-CHAOS-001 | валидный префикс, последняя строка игнорируется, восстановление |
| 21 | Падение миграции на каждом DDL, downgrade, restore | TS-MIG-001 | атомарность, `schema_version`, данные не потеряны |
| 22 | DNS rebinding/CSRF к control API, токен в query, pipe | TS-CTL-001 | отказ по `Host`/`Origin`/`Sec-Fetch-Site`; токен только в заголовке |
| 23 | Утечка токенов в лог/URL/Referer/CSV/JSONL | TS-SEC-001 | сканер чист; секрет не найден |
| 24 | Удаление аккаунта с активными устройствами/агентами | TS-AUTH-008 | каскад/анонимизация, нет сирот, grace/отмена |

**Техника:** race — `threading.Barrier`/`asyncio.Event`, не `sleep`; property-based — `hypothesis` для `state/nonce/code/workspace/JSONL`; fuzz — корпуса OWASP для SQLi/XSS + случайные аргументы инструментов; миграции — failpoint после каждого statement + `kill -9`; повреждение — обрезка файла на каждом байте/строке, невалидный UTF-8, гигантская строка.

---

## 10. Q7. Обязательные ручные тесты

TEST_PLAN уже помечает 11 TRY-кейсов как `manual`; ниже — что обязано остаться ручным и как фиксировать доказательство.

| ID | Что | Почему только вручную | Доказательство |
| :-- | :-- | :-- | :-- |
| M-AUTH-01 | Реальный вход через Google на prod-домене | Нужны реальные client id/secret, consent, антибот; fake IdP не воспроизводит quirks | Видео/скриншоты + `state`/`nonce` в логе + подпись SEC |
| M-TRAY-01 | Автозапуск после реальной перезагрузки Windows | Нужен logon/реестр/планировщик; CI не перезагружается | `reg query HKCU\...\Run`, скрин после ребута |
| M-TRAY-02 | DPI 125/150/200%, два монитора | Композитор/масштабирование реального железа | Скриншоты меню/иконки, координаты |
| M-TRAY-03 | Установка без админа, SmartScreen/антивирус | Реальные UAC/подпись/AV-эвристики | Скриншоты предупреждений, `whoami /groups` |
| M-TRAY-04 | Тосты и screen reader | Реальные уведомления/AT (NVDA/Narrator) | Видео/скрин + версия AT |
| M-CAB-01 | Мобильный Safari/Chrome, viewport | Реальные cookie-политики и тач | Скриншоты 390×844 |
| M-NET-01 | Captive portal/offline/прокси | Реальный сетевой стек | Лог outbox, скрин ошибки |
| M-USE-01 | Потеря питания при записи/ротации, реальный ACL | Файловая система и питание | `icacls`/`getfacl`, `fsck`, скрин |
| M-OPS-01 | Апгрейд с 0.4.8 на реальных данных + откат | Реальные данные/установка | Diff БД, restore-лог, подпись OPS |
| M-MAC-01 | macOS/Linux трей | Wave 2; отложить | — |

**Формат доказательства** (`artifacts/<WBS>/M-*/manual-YYYYMMDD-<initials>.md`):

```markdown
# M-TRAY-01 — автозапуск после перезагрузки
- WBS: TRY-08, TRY-09
- Build: v2.0.0-rc1, sha256:...
- Машина: Windows 11 23H2, x64; пользователь без админ-прав
- UTC: 2026-11-20T10:00:00Z
- Шаги: 1) ... 2) ...
- Ожидание: ...
- Факт: ...
- Скриншоты: shot-01-registry.png, shot-02-after-reboot.png
- Логи: ... (фрагмент, без секретов)
- Исполнитель: DESK; Reviewer: QA — APPROVED; sha256 файла: ...
```

Чекер проверяет обязательные поля, существование изображений, `reviewer != owner`, дату и коммит; подпись дублируется в `REVIEW_LOG`.

---

## 11. Q8. 40 тест-кейсов и правила именования

### 11.1. Правила

- **Конформанс:** `T-<AREA>-<NN>` (уже 158 строк, 1:1 WBS). Не переиспользовать; retired — отдельный статус.
- **Сценарий/атака:** `TS-<AREA>-<NNN>` (этот раздел, 40 кейсов, ≥2 WBS).
- **Ручной:** `M-<AREA>-<NN>` для кросс-среза; строки `T-TRY-*` остаются manual.
- **pytest-node:** `tests/test_<area>.py::test_<verb>_<object>_<condition>`; параметры — стабильный ASCII (`[expired]`, `[cross-tenant]`).
- **Маркеры:** `@pytest.mark.test_id("TS-AUTH-001")`, `.wbs("AUTH-02")`, `.level("security")`, `.blocking("P0")`.
- **Один тест — один primary WBS**, остальные — в списке; Test ID в commit message/PR.
- **P0** — security/данные/релиз; **P1** — UX/деградация; красный P0 = стоп G5.

### 11.2. Кейсы

| ID | WBS | Уровень | Шаги (кратко) | Ожидание | Блок. |
| :-- | :-- | :-- | :-- | :-- | :--: |
| TS-AUTH-001 | AUTH-02, AUTH-07 | security | `start` → callback с отсутствующим/чужим/повторным `state` и `nonce` | 400/403, сессии нет, событие аудита | P0 |
| TS-AUTH-002 | AUTH-05, AUTH-06 | security | JWT: `alg=none`, HS256, неизвестный `kid`, JWKS 500/timeout, ротация | Отказ; один refresh; fail-closed | P0 |
| TS-AUTH-003 | AUTH-06, AUTH-08 | unit/contract | `exp/iat` ±60/61, чужой `aud/azp/iss`, `email_verified=false`, регистр email | Граница 60 с; отказ; нормализация; нет дубля | P0 |
| TS-AUTH-004 | AUTH-03 | security | `next` = `//evil`, `https://evil`, `/\evil`, `%2f%2fevil` | Только локальный allowlist | P0 |
| TS-AUTH-005 | AUTH-11…14 | security | Фиксация, подмена cookie, idle/absolute TTL, logout, «везде», параллельные сессии | Новый id; старый невалиден; cookie чистая | P0 |
| TS-AUTH-006 | AUTH-15 | security | POST без/чужим CSRF, `Origin: null`, cross-site, GET-мутация | 403; состояние не изменено | P0 |
| TS-AUTH-007 | AUTH-16 | security | N неудачных логинов по IP/аккаунту; неизвестный email | 429 + `Retry-After`; ответы неразличимы | P0 |
| TS-AUTH-008 | AUTH-18, REV-CLM-13 | e2e/security | Удаление аккаунта с сессиями/устройствами/агентами; подтверждение email; grace/отмена | Каскад без сирот; аудит; отмена работает | P0 |
| TS-CLM-001 | CLM-01, CLM-06 | integration | `pair` с валидным device token → claim в кабинете | Устройство привязано к тенанту; registry согласован; новых ключей нет | P0 |
| TS-CLM-002 | CLM-02, CLM-03 | unit/security | Формат/энтропия; TTL −1/0/+1; повтор; регистр/пробелы | Только валидный неистёкший код, один раз | P0 |
| TS-CLM-003 | CLM-03, CLM-06 | race/integration | Два браузера одновременно вводят один код (barrier) | Ровно один 200, один 409/400, одна привязка | P0 |
| TS-CLM-004 | CLM-04, CLM-09 | security | 10k неверных кодов по IP/аккаунту; параллельно валидный claim | 429; валидный проходит; нет энумерации | P0 |
| TS-CLM-005 | CLM-05, CLM-08 | security | Без сессии, чужой tenant, без CSRF, уже привязанное устройство | 401/404/403 без деталей | P0 |
| TS-AGT-001 | AGT-01, AGT-02 | integration | Два `initialize` одним клиентом; label `<script>`, >64, Unicode | Один агент; `last_used`; экранирование/нормализация | P0 |
| TS-AGT-002 | AGT-03…05 | security | Выпуск ключа; URL один раз; БД; логи; энтропия | В БД только SHA-256; plaintext нигде; повторно не показать | P0 |
| TS-AGT-003 | AGT-06 | security | Комбинации agent-key/legacy/device token; неоднозначность; revoked | Приоритет; 401; корректный `agent_id` | P0 |
| TS-AGT-004 | AGT-08 | e2e/race | Отзыв во время `bash_exec`; следующий вызов; другой агент | Политика in-flight; 401; другие живы; аудит | P0 |
| TS-AGT-005 | AGT-07, OPS-04 | integration | Pre-v2 registry без `link_key`, старый URL/hello | Агент `default`; URL работает; отзыв default не ломает других | P0 |
| TS-USE-001 | USE-01, USE-11 | unit/security | Запись событий с аргументами/путями/выводом/секретами; скан JSONL | Только whitelisted-поля; payload отсутствует; UTC ms | P0 |
| TS-USE-002 | USE-04, USE-05 | integration | 10 МБ × 6, конкурентная запись, gzip, retention 90 дней, чтение после ротации | 5 файлов; сортируемые имена; активный не удалён | P0 |
| TS-USE-003 | USE-06 | security | Битая строка, невалидный UTF-8, `\n`/NUL в label/tool, 1 МБ строка | Пропуск с warning; JSONL валиден; фейкового события нет | P0 |
| TS-USE-004 | USE-07…09 | unit/perf | 1/100k событий: counts, p50/p95, фильтры, CSV, формула `=cmd` | Суммы точны; память < 100 МБ; CSV RFC4180 + защита формул | P1 |
| TS-USE-005 | USE-14, REV-USE-16 | security/integration | Ingest: свой токен, чужой `device_id/tenant_id`, replay, ±день, offline-outbox | Device только из токена; идемпотентность; нет cross-tenant | P0 |
| TS-USE-006 | USE-03 | security | POSIX 0600, Windows ACL только пользователя, startup-check, `clear` | Посторонний не читает; после очистки снова 0600 | P0 |
| TS-CAB-001 | CAB-03, CAB-05, CAB-08, CAB-11, SEC-06 | security | Два аккаунта A/B: все маршруты с ID B, cursor-пагинация | 404 идентичен «нет»; B не изменён; нет утечки | P0 |
| TS-CAB-002 | SEC-03, SEC-04 | security | SQLi в поиске/сортировке; XSS в имени/метке/workspace/ошибке; CSP | Параметризация; экранирование; CSP без inline | P0 |
| TS-CAB-003 | CAB-04, CAB-05, CAB-12 | integration | Rename trim/длина/Unicode/control; revoke с именем; идемпотентность; вставки при пагинации | Валидация; аудит; стабильный cursor | P1 |
| TS-CAB-004 | CAB-14 | a11y/i18n | axe на 6 страницах; ru/en parity; плюрализация; даты; keyboard/aria-live; 390 px | 0 critical; нет missing keys; фокус виден | P1 |
| TS-CTL-001 | CTL-01…03, CTL-10 | security | Нет/чужой токен; bind `0.0.0.0`; `Host`/`Origin` evil; `Sec-Fetch-Site`; query-токен; pipe ACL | Только loopback, только заголовок, отказ | P0 |
| TS-CTL-002 | CTL-05 | integration | SSE: порядок/heartbeat; disconnect/reconnect; медленный клиент; 10 событий/с | Нет блокировки; состояние восстанавливается; нет dup/loss | P0 |
| TS-CTL-003 | CTL-06…09 | integration | Частичный PATCH с невалидными полями; pause → `node_paused`; in-flight; unpause; второй экземпляр | 400 с полем; поведение; один сервер | P0 |
| TS-MOD-001 | MOD-02…06 | security | Все `TOOLS` × `read_only/agent` × scopes × bridge/node/direct/legacy; `MESH_READ_ONLY=1` | Ни одного обхода; `tools/list` согласован; `mode_denied` | P0 |
| TS-MOD-002 | MOD-07 | security | Workspace: `..`, symlink, junction, UNC, длинный путь, кириллица/пробелы, удалённая папка | Fail-closed; cwd внутри; `workspace_unavailable` | P0 |
| TS-MIG-001 | OPS-01, OPS-02 | migration | Fresh/up/down/idempotent; crash после каждого DDL; duplicate column; backup/restore на чистую | Атомарность, `schema_version`, 0 потерь | P0 |
| TS-OPS-001 | OPS-03, OPS-04, AUTH-17 | integration | `MESH_ACCOUNT=0`; нет `jinja2/PyJWT/httpx`; dev на public bind; узел 0.4.x | MCP жив; `/account` «не настроен»; отказ dev; совместимость | P0 |
| TS-PERF-001 | CAB-01, TRY-06, CTL-05, USE-08 | perf | 100 устройств/10k событий, 10k строк журнала, 20 параллельных вызовов, медленный SSE | p95 < 200 ms; память в бюджете; без фризов | P1 |
| TS-CHAOS-001 | USE-02, USE-06, CTL-05, OPS-07 | chaos | `kill -9` при append; `ENOSPC`; DB locked/corrupt; обрыв сети; рестарт шлюза | Валидный префикс; восстановление; нет частичных данных | P0 |
| TS-SEC-001 | SEC-02, SEC-08 | security | gitleaks/bandit/pip-audit; скан логов/артефактов/CSV/JSONL/URL; короткий `MESH_SESSION_SECRET` | 0 секретов; слабый секрет → отказ старта | P0 |
| TS-E2E-001 | AUTH, CLM, CAB, AGT | e2e | Fake IdP: логин → claim → агент → rename → revoke → usage → logout; два аккаунта; mobile viewport | Сценарий зелёный; trace/скрин; изоляция | P0 |
| TS-TRAY-001 | TRY-01…09, TRY-11 | integration/manual | Avalonia headless: состояния < 200 мс, меню; HKCU Run write/read/remove; сосуществование с Startup `.vbs`; 100/125/150% DPI; без админа | Состояние и поведение совпадают; нет дублей автозапуска | P0 |

`P0` — красный блокирует G5; `P1` — блокирует при деградации/по условию (`TS-CAB-004` — любой critical axe; `TS-PERF-001` — выход за бюджет).

---

## 12. Предложение по владению

| Область | Автор тестов | Ревьюер (не автор) | Второй ревьюер |
| :-- | :-- | :-- | :-- |
| AUTH, CLM, SEC | SEC | QA | C (red team) |
| CAB, i18n/a11y | UI | QA | SEC (XSS/CSRF) |
| AGT, MOD, USE, миграции | BE | QA | SEC |
| CTL, SSE | DESK + BE | QA | PERF |
| TRY, autostart, DPI | DESK | QA | UI |
| CI, артефакты, окружения | OPS | QA | P |
| Perf/chaos | PERF + QA | P | BE |

QA владеет `TEST_PLAN`, `traceability.yaml`, `check_team_coverage.py`, политикой flaky и приёмкой доказательств; **не** ревьюит собственные тесты (`AGENT_TEAM.md:40`). SEC имеет вето по Critical/High, UI — по broken UX, OPS — по отсутствию отката. P подписывает G0 только при наличии строки WBS, теста и владельца; сейчас 44 REV-строки этот критерий нарушают.

---

## 13. Чек-лист готовности домена (что я проверю лично перед подписью)

1. `pytest --collect-only -q` = число тестов в матрице; 0 orphan-тестов без `test_id`.
2. `python tools/check_team_coverage.py --strict` → exit 0; **167/167**, включая все `REV-*`; у всех high-risk есть unit+integration+security.
3. Чистое окружение без сети: fake-IdP security-набор (TS-AUTH-001…006, TS-CLM-003/004) зелёный.
4. Coverage: line ≥80, branch ≥70, critical ≥90/85; `diff-cover` не ниже 80.
5. `TS-MIG-001` на копии прод-данных: up → crash → reopen → down → restore; diff схемы и контрольные суммы.
6. `TS-CAB-001` (IDOR) и `TS-CAB-002` (XSS/SQLi) на двух аккаунтах; 404 неотличим; CSP-заголовки.
7. Playwright E2E: trace/видео/скриншоты приложены; мобильный viewport; два аккаунта изолированы.
8. Ручные `M-*`/manual: файлы подписаны, `reviewer != owner`, скриншоты открываются, секретов нет.
9. `gitleaks` + скан `artifacts/`, логов, `usage.jsonl`, CSV — 0 находок; слабый `MESH_SESSION_SECRET` отклоняется.
10. Flaky-аудит: P0-набор 3× без вариаций; 0 `sleep > 100 ms`; таймауты и карантин работают.

---

## 14. Что не проверено

- Полный прогон на этой машине: 3 падения в `tests/test_rename_compat.py` из-за bash/WSL (`E_ACCESSDENIED`), 4 skip; к Wave 1 не относятся, но подтверждают необходимость маркировки environment-dependent тестов. На CI-матрице не перепроверял.
- Реальный Google OIDC, production-домен, cookie-политики Safari — нет доступа.
- Ребут Windows, автозапуск, DPI/мультимонитор, screen reader, SmartScreen — нет стенда.
- macOS/Linux-трей — вне Wave 1; не проверял.
- `TEST_PLAN.md`/`OWNERSHIP_MATRIX.md`/`WBS.md` менялись во время ревью (параллельные агенты); отчёт фиксирует состояние на момент завершения, а не итог.
- Числа производительности (p95, память) не измерялись; это бюджеты, а не факты.

---

**Итог:** бумажный контур стал заметно сильнее, но «проверка каждой мелочи» появится только тогда, когда WBS-строка, тест и доказательство станут одной машинно-проверяемой записью. Сейчас чекер красный (9–44 `REV-*` без тестов/владельцев), а 158 «тестов» не привязаны ни к одному pytest-node.
