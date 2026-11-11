# Process-auditor: TDD, независимое ревью и доказательство в Wave 1

**Дата:** 2026-11-11, снимок 04:40
**Роль:** Process Auditor (методология Superpowers)
**Область:** план Wave 1, WBS/матрица/тест-план, протокол верификации, процесс ревью
**Читал:** `docs/wave-1-auth-account-log-settings.md` (ред. 04:35), `docs/remote_harness_mcp_v2_plan.md` §1.6–1.7, §4.5, §7, §9, E3/E4; `AGENT_TEAM.md`, `WBS.md` (ред. 04:35), `TEST_PLAN.md` (ред. 04:36), `OWNERSHIP_MATRIX.md` (ред. 04:31), `VERIFICATION_PROTOCOL.md`, `REVIEW_LOG.md`, `REVIEW_BRIEF.md`, `tools/check_team_coverage.py` (ред. 04:34), `tools/build_team_matrix.py` (ред. 04:35); upstream-скиллы `writing-plans`, `test-driven-development`, `subagent-driven-development`, `verification-before-completion`, `requesting-code-review`; `gateway.py`, `core/mcp_tools.py`, `.gitignore`, `.github/workflows/ci.yml`, `tests/`.
**Динамика:** рабочая копия менялась во время аудита (04:29–04:42). План успел получить заголовок, Global Constraints, Review Focus и Execution Model — это закрыло часть замечаний. Одновременно WBS вырос до 155 строк (111 + 44 REV), матрица осталась на 111, генератор падает, checker красный. В 04:42 появился `.github/workflows/team-coverage.yml`, который гоняет тот же слабый checker — слабая проверка стала CI-гейтом. Всё ниже — по снимку 04:40; перед подписью проверки надо перезапустить.
**Не проверено:** живой Google OIDC, сборка Avalonia, Windows ACL, прогон gateway — read-only аудит документов и репозитория.

---

## 1. Вердикт

**NOT_READY** — процесс описан лучше, но гейт физически красный, а доказательства не производятся.

Что стало правильно: у плана есть `For agentic workers`, Goal/Architecture/Tech Stack/Spec, Global Constraints с точными значениями (claim 8 символов, TTL 10 мин, `__Host-rhm_session`, `ak_`+43 base64url), Review Focus из 5 строк и Execution Model с fresh implementer/reviewer, diff `BASE..HEAD` и fix-loop ≤ 5. `TEST_PLAN` для 111 исходных строк стал конкретным: команды `pytest`, входные значения, точные ожидания. Это уже не «TBD».

Почему всё равно NOT_READY:

1. **44 новые WBS-строки (`REV-*`) не имеют ни владельца, ни теста, ни строки в матрице.** `check_team_coverage.py --strict` → `exit 1`, 44 «no ownership row» + 4 домена без тестов. Гейт G5 по протоколу не может быть пройден.
2. **Генератор матрицы не работает на текущем WBS:** `build_team_matrix.py` → `KeyError: 'UI'` (в `DOMAIN` нет `UI`/`DOC`). Матрица и тест-план не перегенерируются, 44 строки останутся сиротами механически.
3. **Классический false-green уже был:** в 04:35 checker печатал «Coverage complete» и `exit 0` при 111/111, нулевой реализации и отсутствующем `artifacts/` — он не открывает evidence. Сейчас он красный только из-за сирот, а не из-за отсутствия доказательств.
4. **Доказательства некоммитируемы:** `artifacts/` нет, `.gitignore:3` (`*.log`) игнорирует путь `artifacts/<WBS>/T-<WBS>.log`, который матрица называет evidence.
5. **Процесс ревью описан в двух местах по-разному:** план требует fresh implementer/reviewer, diff `BASE..HEAD` и cap ≤ 5, устав — одного ревьюера с двумя вердиктами и без fresh/diff/cap/мета-ревью. Бюджет ревью в §11 появился (30–45 pd), но это 0,19–0,29 pd на строку при 155 строках — меньше, чем требует собственный протокол (прочитать brief, прогнать тест, проверить red).

Начинать Sprint 1 сейчас — получить красный чекер, падающий генератор и отчёты «сделано» без файлов.

---

## 2. Соответствие `writing-plans` (вопрос 1)

| Требование | Статус | Доказательство (04:40) |
| :--- | :--- | :--- |
| Заголовок: Goal / Architecture / Tech Stack / Spec | **да** | `wave-1…:10-18` |
| Пометка «For agentic workers + REQUIRED SUB-SKILL» | **да** | `:10` |
| Global Constraints с точными значениями | **да** | `:20-28` (8 пунктов) |
| Review Focus — 5 классов ввода + тест в задаче | **да** (5 строк), тесты привязаны не к задачам, а к WBS | `:30-36` |
| Execution Model: fresh implementer/reviewer, diff, cap | **да** | `:38-44` |
| Путь `docs/superpowers/plans/YYYY-MM-DD-*.md` | **нет** | файл в `docs/`, без даты |
| File Structure: карта файлов и ответственности | частично | компоненты `:130-144`; модулей внутри `account/` нет |
| Task right-sizing | частично | 155 WBS-строк; часть — эпики (AUTH-16, CAB-01, TRY-11) |
| Files: Create/Modify/Test с точными путями | **нет** | WBS не содержит файлов; briefs обещаны, но не созданы (`:40`) |
| Interfaces: Consumes/Produces с сигнатурами | **нет** | `SessionContext` противоречит E3; см. M-03 |
| Steps: Red → fail → Green → pass → Commit | **нет в плане** | вынесено в briefs (`:40`), которых нет; в `TEST_PLAN` есть команды, но нет red/commit |
| Нет «TBD / обработать edge cases» | почти | формат ID (`01J`/`ag_`), граница суток, имя gzip, `/pair?code=` не решены |

**Вывод:** заголовок и глобальные ограничения закрыты — это большой шаг. Но `writing-plans` требует «exact interface and exact test» в самом плане; сейчас есть точный тест (для 111 строк) и нет интерфейсов/файлов, а 44 новые строки не имеют даже теста.

---

## 3. Findings

| ID | Severity | Область | Проблема | Доказательство | Рекомендация | Владелец |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **B-01** | Blocker | task briefs | Brief'ов нет: план обещает, что Planner создаст `Files/Interfaces/Red/Green/command/commit` на каждую строку, но ни одного brief не существует. WBS-строка — работа в одну фразу, свежий implementer не знает ни файла, ни сигнатуры | `wave-1…:38-44`; `WBS.md:13-223`; `WBS` (grep: нет `Files`, `Interfaces`) | Создать brief-шаблон + пример на 2 строки (AUTH-02, CLM-03); без brief строка не стартует (G0) | P |
| **B-02** | Blocker | сироты ревью | 44 `REV-*` строки не имеют owner/reviewer/test/матрицы. Checker красный, гейт G5 невозможен | запуск 04:38: `WBS 155, ownership 111, tests 111, 111/155`, `exit 1`, 44 «no ownership row» | Прогнать генератор после фикса домена; назначить владельцев/ревьюеров; добавить тесты на 44 строки | P + QA |
| **B-03** | Blocker | генератор | `build_team_matrix.py` падает `KeyError: 'UI'`: в `DOMAIN` нет `UI` и `DOC`, хотя REV-UI-* и REV-DOC-01 уже в WBS. Матрица не перегенерируется механически | воспроизведено на копии: `KeyError: 'UI'`; `build_team_matrix.py:20-33`, `:54-57` | Добавить `UI`, `DOC` (и владельцев), покрыть тестом генератора; запретить merge WBS без зелёного генератора | QA + P |
| **B-04** | Blocker | доказательства | `artifacts/` отсутствует; `.gitignore:3` игнорирует `*.log`; путь evidence из матрицы (`artifacts/<WBS>/T-<WBS>.log`) не может быть закоммичен. Новый CI-джоб `team-coverage` проверяет только ID, а не файлы | `git check-ignore -v artifacts/AUTH-01/T-AUTH-01.log` → ignored; `OWNERSHIP_MATRIX.md:8`; `.github/workflows/team-coverage.yml` | `!artifacts/**` + `artifacts/.gitkeep`; JUnit XML/manifest как обязательные; чекер и CI проверяют файл на диске | OPS |
| **C-01** | Critical | дрейф артефактов | План (04:35), WBS (04:35), тест-план (04:36) и матрица (04:31) расходятся: план требует claim только с device token, `T-CLM-01` проверяет claim без токена; WBS 155 строк, план говорит «143», `TEST_PLAN` — «111, 1:1»; матрица 111 | `wave-1…:18`, `:187`; `WBS.md:227`; `TEST_PLAN.md:3`; `T-CLM-01` (`TEST_PLAN.md:27`) | Один источник: WBS; перегенерировать всё; checker сверяет счётчики plan/WBS/matrix/tests | P |
| **C-02** | Critical | модель данных | Схема не обновлена под новые требования: в `device_claims` нет `pair_secret_hash`, `fingerprint`, `status`, `claimed_by`; код открыт; нет consent/share-поля; `usage_daily` без `account_id`/FK; ingest-пейлоад без `agent_id`; `agents.scopes` DEFAULT `files:read` при read_only с `system_info` | `wave-1…:428-434`, `:252-259`, `:389-401`, `:404-415`, `:687-698` | Дописать колонки/таблицы; определить mode × scopes; привести ingest к PK; тест удаления аккаунта без сирот | BE + SEC |
| **C-03** | Critical | трассировка | Нет `Plan anchor`; 44 REV-строки не связаны с отчётами ревьюеров; REV-зависимости ссылаются на несуществующие `API-04`, `QA-01`, `VERIFICATION` — checker зависимости не проверяет | `WBS.md:189`, `:218`, `:221-222`; `check_team_coverage.py` (нет проверки deps) | Колонка `Plan anchor` + `Source finding`; checker валидирует существование зависимостей и отчётов | QA + P |
| **C-04** | Critical | два устава | План уже требует fresh reviewer, diff `BASE..HEAD`, cap ≤ 5 (Execution Model). Устав всё ещё: один ревьюер даёт spec+quality, без fresh/diff/cap/ledger/мета; «кто ревьюит ревьюера» не определено ни там, ни там | `wave-1…:38-44`; `AGENT_TEAM:88`, `:125`; `VERIFICATION_PROTOCOL:68-78` | Обновить `AGENT_TEAM` до Execution Model; один источник процесса; добавить мета-ревью | P |
| **C-05** | Critical | независимость | Матрица хранит роль-код: `AUTH→BE/SEC` (18 строк), `CAB→UI/API` (14), `USE→BE/QA` (14), `CTL→BE/DESK` (10); нет `Reviewer2`, нет свежести; CAB-04/05 (XSS/IDOR) ревьюит API, CTL-токен — DESK | `OWNERSHIP_MATRIX.md:8-118`; `build_team_matrix.py:20-33` | Именованные агенты из ростера; второй ревьюер для security; лимит строк на ревьюера; перераспределить CAB/CTL/USE | P + SEC |
| **C-06** | Critical | верификация | Протокол не tamper-evident: нет manifest, sha, replay; прогон ревьюера не сохраняется; матрица требует один `.log`, протокол — red+green+JUnit; ручные M-01…M-08 не связаны с `TEST_PLAN` | `VERIFICATION_PROTOCOL.md:11-35`, `:68-78`, `:107-118`; `OWNERSHIP_MATRIX.md:8` | `evidence.json` (commit, cmd, exit, sha256, reviewer), replay, M-ID в тест-плане | QA + OPS |
| **C-07** | Critical | тест-план как контракт | Нет структурных полей `NodeID`, `Artifact`, `Red proof`, `Owner`, `Verified sha`; `Expected` — проза с несколькими ассертами; `T-AUTH-03` использует несуществующий флаг `pytest --parametrize`; 44 REV-строки без тестов | `TEST_PLAN.md:7-9`, `:11`; `:119` (111 строк) | Добавить поля; заменить `--parametrize` на `@pytest.mark.parametrize`; тест-план на 155 | QA |
| **C-08** | Critical | гейт релиза | Все 111 существующих тестов `Blocking=yes` (на 155 строк), из них 11 TRY — manual-only Windows 11 без оператора/порога; Playwright и .NET в CI отсутствуют; гейт либо не закроется, либо его обойдут | `TEST_PLAN.md:84-94`; `.github/workflows/ci.yml` (нет playwright/dotnet) | Blocking только gate-critical; manual-контракт; CI-джобы Playwright/.NET | QA + OPS |
| **M-01** | Major | CI | Нет `pytest-cov`, Playwright, .NET, `jinja2`, `PyJWT`, fake OIDC; `--cov-fail-under=80` из протокола негде выполнить | `.github/workflows/ci.yml`; `VERIFICATION_PROTOCOL:50` | Обновить CI до Sprint 1 | OPS |
| **M-02** | Major | coverage | «Покрытие нового кода ≥ 80%» не измеряется: `--cov=core` считает legacy, baseline и артефакт не заданы | `wave-1…` §10 DoD; `VERIFICATION_PROTOCOL:50` | `diff-cover` по изменённым файлам + порог + upload | QA |
| **M-03** | Major | интерфейс режима | v2 §1.6 (п. 6–7): `bridge/` и `SessionContext` в первые 2 недели; Wave 1: `SessionContext` в Sprint 3, gateway не режется. v2 E3 «Изменения»: `configure_session()` + `bridge/policy.py` + `session_start`; MOD-01: контекст на вызов | `v2` §1.6, E3 «Изменения» и шаг 2; `wave-1…` Sprint 3; `WBS.md:137` | Один контракт (ctx в `tools/call` + policy на узле), обновить E3/§1.6 | A |
| **M-04** | Major | миграции | `.sql` + `schema_version` против Alembic (v2 §4.5); OPS-01 не говорит механизм | `wave-1…:437`; `v2:299`; `WBS.md:163` | Выбрать `.sql`, `account.migrate --up/--down` (как в протоколе) | BE |
| **M-05** | Major | approvals | `REVIEW_LOG` пуст (0 `APPROVED`), в матрице нет `Status`/`Approval`, checker не читает подписи | `REVIEW_LOG.md:42-46`; `OWNERSHIP_MATRIX.md:6` | Колонки `Status`, `Approval`; checker валидирует подпись и diff sha | QA |
| **M-06** | Major | бюджет ревью | В §11 добавлено «Ревью (task + final) 30–45 pd» на 155 строк = 0,19–0,29 pd (1,5–2,3 ч) на строку; протокол требует прочитать brief, прогнать тест, проверить red и diff — это не 2 часа; финальный review включён в ту же вилку, отдельной оценки нет | `wave-1…` §11 (строки таблицы 597–607) | Пересчитать по 0,5 pd/строку или сократить число строк; отдельная строка на финальный review | P |
| **M-07** | Minor | оценки | Заголовок: 120/138 pd и 4–4,5 FTE; таблица §11: 154–169 pd и 5–5,5 FTE; план говорит «WBS 143 строки», в WBS — 155 | `wave-1…:5-6`, §11; `WBS.md:227` | Согласовать все три числа | P |
| **M-08** | Minor | открытые решения | Формат ID (`01J` vs `ag_`), граница суток, имя gzip, страница `/pair` — не решены | `wave-1…` §6, §13 | Внести в Global Constraints | A |

**Итого:** 4 Blocker, 8 Critical, 6 Major, 2 Minor.

---

## 4. Гранулярность: где шаг принимает несколько решений (вопрос 2)

План делегирует нарезку Planner'у, но WBS-строки всё ещё требуют 3–6 решений каждая:

1. `AUTH-16` — лимиты по IP и аккаунту, окно, 429, lockout N, анти-энумерация, где хранится счётчик.
2. `AUTH-18` — подтверждение email, каскад, отвязка устройств, аудит, grace-период.
3. `CAB-01` — корректность данных + `< 200 мс на 100 устройствах` (функциональность и перф в одной строке).
4. `USE-04` — 10 МБ×5, gzip, неблокирующая ротация, сортируемые имена, гонка с читателем.
5. `USE-09` — фильтры + CSV (BOM, экранирование, стриминг, имя файла).
6. `TRY-11` — single-file publish, DPI, i18n, подпись, crash-лог — пять решений, разные инструменты.
7. `MOD-01` — контекст на вызов + отсутствие протечки между агентами (архитектура + конкурентный тест).
8. `OPS-05` — матрица Python/OS + `< 10 мин` + flaky=fail.
9. `REV-SEC-09` — reserved names + mount order + CORS + `Vary` (четыре независимых решения).

Правило скилла: «step is done when the implementer can write exactly one reasonable thing». Без brief'а с файлом и сигнатурой ни одна строка этому не удовлетворяет.

---

## 5. TDD-покрытие задач (вопрос 3)

| Домен | Строк | Есть тест-кейс | Есть команда | Есть ожидаемый exit | Есть red proof | Есть commit |
| :--- | ---: | :---: | :---: | :---: | :---: | :---: |
| AUTH | 18 | 18 | 18 | текст, не exit | 0 | 0 |
| CLM | 10 | 10 | 10 | текст | 0 | 0 |
| CAB | 14 | 14 | 14 | текст | 0 | 0 |
| AGT | 10 | 10 | 10 | текст | 0 | 0 |
| USE | 14 | 14 | 14 | текст | 0 | 0 |
| CTL | 10 | 10 | 10 | текст | 0 | 0 |
| TRY | 11 | 11 (manual) | чек-лист | текст | 0 | 0 |
| MOD | 8 | 8 | 8 | текст | 0 | 0 |
| SEC | 8 | 8 | 8 | текст | 0 | 0 |
| OPS | 8 | 8 | 8 | текст | 0 | 0 |
| **REV** | **44** | **0** | **0** | **0** | **0** | **0** |

**Факт:** 111/155 имеют тест-кейс и команду (это уже TDD-пригодно), но **0/155** имеют red proof и commit, **44/155** не имеют ничего. Ни одна строка не содержит кода падающего теста; `TEST_PLAN` даёт шаги словами, а не тест-код. Проверить «тест падал до реализации» негде: поля `red proof` нет ни в матрице, ни в тест-плане, ни в checker.

Минимальный шаблон brief, который надо внедрить (пример для CLM-03):

```markdown
### CLM-03: одноразовость кода
**Files:** Create `account/claim.py`; Modify `account/db.py`; Test `tests/test_device_claim.py`
**Interfaces:** Produces `consume_claim(conn, code: str, *, now: datetime) -> ClaimResult`
- [ ] Step 1. Тест `test_second_claim_returns_409` (код в плане)
- [ ] Step 2. `python -m pytest tests/test_device_claim.py::test_second_claim_returns_409 -q`
      Expected: FAIL `KeyError: 'consume_claim'`
- [ ] Step 3. Реализация
- [ ] Step 4. Та же команда → PASS; `python -m pytest -q` → PASS
- [ ] Step 5. Commit `feat(claim): single-use code`
```

---

## 6. Процесс ревью: что помогает, что мешает, кто ревьюит ревьюера (вопрос 4)

**Помогает:**
- План, Execution Model: свежий implementer и свежий reviewer, diff `BASE..HEAD`, fix-loop ≤ 5, финальный whole-branch review (`wave-1…:38-44`).
- `VERIFICATION_PROTOCOL:68-78` — ревьюер обязан сам прогнать тест; `APPROVED` без запуска запрещён.
- `:22` — артефакт без команды и вывода не доказательство.
- `AGENT_TEAM:40` — владелец ≠ ревьюер; `:44-60` — именованный ростер и backup.
- `AGENT_TEAM:97-101` — запрет закрывать по «готово» и удалять тест без записи.
- `REVIEW_LOG:3` — «пустой журнал = ревью не проводилось»; сейчас это честно: подписей 0.

**Мешает:**
1. `AGENT_TEAM:88` — один ревьюер и один проход spec+quality; adapter rule 3 требует двух; план уже требует task-reviewer с двумя вердиктами — устав не обновлён.
2. Роль-код вместо агента в матрице: 18 AUTH-строк ревьюит один SEC, 14 CAB — один API; контекст протекает.
3. Нет `Reviewer2` для security-строк (CAB-04/05, CTL-02/03/10, USE-11/14).
4. Нет ledger: после компакции контроллер не восстановит, какие строки закрыты.
5. Нет мета-ревью: QA проверяет evidence, но не качество ревью; finding без воспроизводимого доказательства не отбраковывается.
6. `AGENT_TEAM:125` — неоднозначность решает Critic + Planner; Critic сам автор findings, Planner — автор плана.
7. Нет правила, что ревьюер получает brief + report + diff, а не историю сессии.

**Кто ревьюит ревьюера:**
- **QA** — 100% Blocker/Critical: существует ли `файл:строка`, совпадает ли цитата, воспроизводим ли сценарий; иначе `unsubstantiated`.
- **Critic** — слепой сэмпл 20% Major/Minor и 100% security; не арбитрирует свои находки.
- **Final whole-branch reviewer** — сильнейшая модель, не ревьюила ни одной строки; получает merged findings + rulings + diff; его вердикт — ворота.
- **SEC** — вето на понижение severity security-находок.
- **Planner** — только фиксирует rulings; не решает по своим разделам плана.

---

## 7. Verification: достаточно ли протокола и матрицы (вопрос 5)

**Нет.** Протокол описывает правильные шаги, но не является доказуемым контрактом:

1. Нет привязки к коммиту: `red-<date>.log` может быть от любой версии кода, sha не фиксируется.
2. Нет `manifest.json` (base/head, команды, exit, хеши логов) — сверять нечего.
3. Прогон ревьюера не сохраняется, хотя протокол его требует (`:73`).
4. Матрица требует один `T-<WBS>.log`, протокол — red+green+JUnit; они противоречат друг другу.
5. `artifacts/` нет; `.gitignore:3` делает evidence некоммитируемым.
6. Checker не открывает evidence; в 04:35 он дал `exit 0` при нулевом коде и пустом `artifacts/`.
7. Ручные M-01…M-08 не имеют ID в `TEST_PLAN` и не связаны со строками.
8. Coverage меряет legacy (`--cov=core`), а DoD говорит «нового кода».

**Обязательные артефакты (минимум на строку):**

| # | Артефакт | Содержание | Проверка |
| :--- | :--- | :--- | :--- |
| A1 | `evidence.json` | wbs, base_sha, head_sha, nodeid, command, exit, log_sha256, reviewer_run | схема + sha |
| A2 | `red.log` | команда, cwd, sha, exit ≠ 0, ожидаемый текст | replay: exit ≠ 0 |
| A3 | `green.log` | exit 0, полный вывод; отдельно полный `pytest -q` | replay: exit 0 |
| A4 | `regress.log` | соседние тесты + полный suite | replay |
| A5 | `diff.patch` + `review-package.md` | `git log/diff --stat/-U10 BASE..HEAD` | diff sha = head |
| A6 | `coverage.xml` | покрытие изменённых файлов | `diff-cover ≥ 80` |
| A7 | `manual-<date>.md` | оператор, машина, шаги, до/после, порог | ревьюер + QA |
| A8 | `REVIEW_LOG` APPROVED | reviewer id, diff_sha, открытые findings | checker |
| A9 | CI run ID | неизменяемый, на том же head | ссылка + sha |
| A10 | checker self-test | удаление A1–A7 → checker red | mutation-тест |

Правило: evidence без replay на recorded `head_sha` считается отсутствующим — это и есть «evidence before claims».

---

## 8. Риски 7 параллельных ревью и протокол сведения (вопрос 6)

**Риски:** 7 отчётов × 10–25 findings без общей нумерации; дубли (schema-находки уже есть у SEC и у меня); взаимоисключающие советы; отсутствие арбитра; расползание scope (Wave 2/3-фичи как блокеры); 7 трактовок severity; отчёты против разных ревизий (план менялся в 04:33 и 04:35; WBS — в 04:35).

**Протокол:**

1. **Фиксация ревизии.** Отчёт указывает sha/время прочитанных файлов; контроллер сводит только отчёты по одной ревизии, остальные — `stale`.
2. **Домены, не документы.** 7 зон без пересечений: OIDC/сессии; claim/legacy; кабинет/IDOR/XSS; журнал/приватность; трей/control API/Windows; режим/scopes/E3; verification/машинная проверяемость. Пересечения — в `cross-cutting`.
3. **ID:** `<ROLE>-F-<nnn>`; severity по брифу: Blocker = нет владельца/теста/решения для must-have §2 либо обходится контроль; Critical = дыра/потеря данных; Major = сломан сценарий; Minor = ясность.
4. **Dedup:** ключ `(якорь, нормализованное утверждение, предлагаемое изменение)`; контроллер сводит в `REVIEW_LOG` с `canonical_id` и `sources:[…]`.
5. **Арбитраж:** каждая сторона прикладывает минимальный воспроизводимый факт; решает владелец домена по RACI; SEC имеет вето на понижение security-severity; решение — `Ruling: R-<nn> — что — почему — цена ошибки`.
6. **Один раунд возражений** 90 минут; далее решение окончательно, dissent — в строке.
7. **Scope guard:** finding вне §2 «Входит» → `wave: 2+`, не блокер; ревьюер не назначает владельцев вне RACI и не требует переписать план целиком.
8. **Бюджет:** 7 × 2 ч + 1 день контроллера; повторное ревью — только по изменённым строкам; `stale` не учитываются.
9. **Генератор:** `build_team_matrix.py` запускает только Planner; после первого `APPROVED` генерация запрещена; генератор обязан работать на текущем WBS (сейчас — KeyError).
10. **Кто ревьюит ревьюера:** QA-мета-аудит (см. §6), запись в `REVIEW_LOG`; финальный reviewer — не из task-ревьюеров.

---

## 9. 10 проверок перед подписью «Wave 1 готова» (вопрос 7)

1. **Полнота:** `check_team_coverage.py --strict` → `exit 0`, 155/155 строк с owner/reviewer/test; 44 REV-строки закрыты.
2. **Генератор:** `python tools/build_team_matrix.py` → exit 0; `git diff --exit-code` после генерации пуст (артефакты не расходятся).
3. **Checker не врёт:** mutation-тест — удаляю `evidence.json` одной строки → checker red; возвращаю → green.
4. **Evidence replay:** 100% green-команд перепрогнаны на `head_sha`, exit 0, `log_sha256` совпал.
5. **Свежий suite:** `python -m pytest -q` → exit 0; тестов ≥ 434 + новые; skip только документированные.
6. **Red-выборка:** 10 случайных строк — откат коммита, названный тест падает с записанным текстом, возврат.
7. **Coverage:** `diff-cover coverage.xml --fail-under=80` → exit 0; артефакт приложен.
8. **Security:** gitleaks чист; IDOR на двух аккаунтах; повтор state/nonce; cookie-флаги; claim rate limit N+1; `pair_secret` без утечки; `MESH_AUTH_MODE=dev` в prod падает; в `usage.jsonl`/`.gz` нет `cat secret`.
9. **Флаг и данные:** `MESH_ACCOUNT=0` → suite зелёный, `/account` 404; миграция с пустой БД и с registry v0.4.8; rollback; удаление аккаунта без сирот; `orphaned` виден.
10. **Ревью:** каждая строка `APPROVED` ревьюером ≠ владельцем (по агенту); QA-мета-аудит; 0 открытых Blocker/Critical; rulings записаны; финальный whole-branch review — не task-ревьюер.

---

## 10. Требования к `OWNERSHIP_MATRIX.md` и `TEST_PLAN.md` (вопрос 8)

Схема — в рамках уже принятых ID (`AUTH-01`, `T-AUTH-01`, `REV-CLM-11`). Генератор — baseline; человеческие колонки не перезаписываются.

### `OWNERSHIP_MATRIX.md`

Колонки: `WBS | Owner | Reviewer | Reviewer2 | Tests | Red | Green | Diff sha | Gate | Status | Plan anchor | Source`

Правила (checker):

1. `WBS` = `^(AUTH|CLM|CAB|AGT|USE|CTL|TRY|MOD|SEC|OPS|REV-[A-Z]+)-\d{2,3}$`, уникален, 1:1 с `WBS.md` (155).
2. `Owner`/`Reviewer` — именованный агент из ростера (`be-auth`, `sec-01`), не роль; `owner != reviewer`; `Reviewer2` обязателен для security-строк.
3. `Tests` — `T-<DOMAIN>-<nn>`; каждый существует; каждый тест использован ≥1 раз.
4. `Red`/`Green` — `artifacts/<WBS>/(red|green)\.log`; файлы существуют; `red.exit != 0`, `green.exit == 0`.
5. `Diff sha` — 40 hex; равен `evidence.json.head_sha`; `git merge-base --is-ancestor` проверен.
6. `Gate` ∈ `G0..G5`; `Status` ∈ `planned|in_progress|review|done|blocked`; `done` требует `REVIEW_LOG` APPROVED и 0 открытых Blocker/Critical.
7. `Plan anchor` — `wave-1…md#L<n>`; строка содержит текст задачи; все чекбоксы плана покрыты.
8. Один ревьюер — ≤ 10 строк и ≤ 3 security-строк.
9. Нет дублей `Detail`; нет `TBD`, «улучшить», «разобраться».
10. Генератор не пишет файл, если в нём есть `APPROVED`.

### `TEST_PLAN.md`

Колонки: `Test ID | WBS | Level | NodeID | Command | Preconditions | Steps | Expected | Mode | Env | Owner | Artifact | Red proof | Verified sha`

Правила:

1. `NodeID` — `tests/<file>.py::<test>[param]`; checker запускает `pytest --collect-only -q` и требует наличия; `Level` ∈ `unit|contract|integration|e2e|security|chaos|perf|manual|migration`.
2. `Command` — литеральная команда из корня, без wildcard, который может смолчать; `--parametrize` запрещён (только `@pytest.mark.parametrize`).
3. `Expected` — `exit <code>; <стабильная подстрока>`; не повторяет критерий WBS дословно.
4. `Steps` — реальные действия с данными; для manual — чек-лист `☐` с наблюдаемым результатом.
5. `Artifact` — путь под `artifacts/`, файл существует; manual — `png|pdf` + оператор + машина + порог.
6. `Red proof` — `red.log` того же `NodeID` с `exit != 0`.
7. `Blocking` — только gate-critical; manual не может быть `Level=unit|contract`.
8. Каждая угроза §9 имеет security-тест; каждый новый тест-файл покрыт; сирот нет.
9. `Verified sha` = HEAD матрицы; устаревший sha → `stale`.
10. `Mode=manual` не закрывается без `manual-<date>.md` с подписью ревьюера.

### Добавить в `tools/check_team_coverage.py`

- `exists()` для Red/Green/Artifact/Diff и `sha256` логов;
- чтение `evidence.json`, сверка `head_sha`;
- `pytest --collect-only` для NodeID;
- `red.exit != 0` + ожидаемый текст;
- чтение `REVIEW_LOG`, валидация APPROVED + diff sha;
- `Plan anchor` и полнота чекбоксов плана;
- валидация зависимостей WBS (сейчас `API-04`, `QA-01`, `VERIFICATION` — битые);
- mutation self-test (`--self-test`), доказывающий, что удаление evidence делает checker red.

---

## 11. Пропущенные детали

| ID | Что не описано | Почему блокирует | WBS-ID |
| :--- | :--- | :--- | :--- |
| D-01 | `pair_secret`/`fingerprint`: хранение, сравнение, TTL, ротация | REV-CLM-11 требует, схемы нет | REV-CLM-11 (колонки) |
| D-02 | `device_claims.status`, `claimed_by`, `attempts`; переходы pending/claimed/expired | Без них статус и анти-энумерация не тестируемы | REV-CLM-12 (колонки) |
| D-03 | `oidc_states(state, nonce, pkce_verifier, next, expires_at, used_at)` | В схеме нет, а state/nonce обязаны проверяться серверно | REV-AUTH-20 (таблица) |
| D-04 | Consent/share: колонка/таблица, default, отзыв, влияние на ingest | «Делиться статистикой» без поля не тестируется | CAB-15 |
| D-05 | `tests/fake_oidc.py`: RSA, JWKS, rotate, владелец | Нужен 18 AUTH-тестам, в WBS нет | AUTH-22 |
| D-06 | `requirements-gateway.txt` + CI-зависимости | Без файла CI-джоб нечего ставить | OPS-09 |
| D-07 | `0001_init.sql` и порядок миграций | «Миграции» есть, начальной схемы нет | OPS-10 |
| D-08 | Семантика mode × scopes | `agents.scopes` DEFAULT противоречит read_only | MOD-09 |
| D-09 | Windows ACL для 0600 и `control.token` | 0600 — POSIX; нужен конкретный ACL | USE-15 |
| D-10 | Playwright и .NET в CI; тест-проект трея | 11 TRY-строк manual и не собираются | OPS-11 |
| D-11 | Manual-протокол: оператор, машина, скриншоты, порог | M-01…M-08 не связаны с тест-планом | QA-01 |
| D-12 | Coverage baseline «нового кода» | `--cov=core` меряет legacy | OPS-12 |
| D-13 | Механизм миграций (`.sql` vs Alembic) | Два документа противоречат | OPS-13 |
| D-14 | `Plan anchor` и `Source finding` для каждой строки | Без них пропуск пункта плана невидим | P-01 |

---

## 12. Пропущенные тесты

| ID | Кейс | Ожидание |
| :--- | :--- | :--- |
| T-REV-CLM-11 | `GET /v1/devices/pair/{code}` без `pair_secret`, с чужим, после TTL | 401/404 без раскрытия статуса |
| T-REV-CLM-12 | Гонка двух claim, поллинг статуса без данных аккаунта | одна привязка, 2–5 с, без утечки |
| T-REV-AUTH-19 | Base URL/redirect: расхождение `PUBLIC_BASE_URL` роняет старт; `?e=` | exit ≠ 0 / 303 с кодом |
| T-REV-AUTH-20 | Шторм state/nonce, повтор после рестарта, step-up ≤ 10 мин | отказ; step-up истекает |
| T-REV-AGT-11 | `ak_`+43 base64url, `lk_`→default, чужой `user=` | 404/401, resolver один |
| T-REV-USE-15 | Usage API: cursor, limit 1–1000, CSV BOM, DELETE counts | контракт совпадает |
| T-REV-USE-16 | Ingest: чужой device, чужой agent, день вне окна, 501-й бакет | 400/401/413 |
| T-REV-CAB-15 | problem+json на каждый код, MCP `isError` | golden-тест 20 кодов |
| T-REV-CTL-11 | stale `control.json`, pid умер, Host/Origin allowlist | трей переподключается, чужой Origin → 403 |
| T-REV-OPS-09 | Старый узел без `hello` при `MESH_ACCOUNT=1` | мутации → `policy_unavailable` |
| T-REV-SEC-09 | Reserved names, mount order, wildcard CORS на `/account` | 404/403, `Vary: Origin` |
| T-REV-SEC-10 | `X-Forwarded-For` от недоверенного прокси, лимиты `(account,ip,device)` | игнор/429 |
| T-REV-SEC-13 | Прямой `bash_exec`/`run_job`/`unshare` в read-only | guard, файл не создан |
| T-REV-SEC-15 | `sha256(link_key)`+prefix, 0600, атомарная запись, порча файла | fail-closed, миграция |
| T-REV-UI-04 | CSV formula injection `=cmd|' /c calc'!A0` | экранировано, числа целы |
| T-REV-PERF-01 | SQLite в async: `to_thread`, WAL, `BEGIN IMMEDIATE`, event loop | loop не блокируется |
| T-REV-QA-02 | `pytest-cov` branch + `diff-cover` + junitxml + upload | пороги в G4/G5 |
| T-REV-QA-05 | `evidence.json` + удаление → checker red | mutation self-test |
| T-OPS-09 | Полнота плана: каждый `- [ ]` имеет WBS-строку | checker red на пропуске |
| T-OPS-10 | Генератор на текущем WBS | exit 0, 0 diff |
| T-SEC-09 | Claim enumeration: 1000 кодов, статус не отличается | 404/429, без утечки |

---

## 13. Предложение по владению

| Блок | Owner | Reviewer | Reviewer2 / мета |
| :--- | :--- | :--- | :--- |
| AUTH (OIDC, сессии, CSRF) | `be-auth` | `sec-01` | `arch-01` + QA |
| CLM (claim, legacy) | `be-auth` | `sec-01` | `api-01` |
| CAB (кабинет, IDOR/XSS) | `ui-cab` | `api-01` | **`sec-01` для CAB-04/05** |
| AGT (ключи, режимы) | `be-data` | `sec-01` | `arch-01` |
| USE (журнал, приватность) | `be-data` | `perf-01` | **`sec-01` для USE-11/14** |
| CTL (control API) | `be-data` | `desk-tray` | **`sec-01` для CTL-02/03/10** |
| TRY (трей, автозапуск) | `desk-tray` | `ui-cab` | QA (manual) |
| MOD (режимы, scopes) | `be-data` | `sec-01` | `arch-01` |
| SEC (сквозная безопасность) | `sec-01` | `critic-01` | QA (evidence) |
| OPS (миграции, CI) | `ops-01` | `qa-01` | `arch-01` (откат) |
| REV-* (44 строки) | по домену суффикса | второй из домена | P распределяет, QA проверяет |
| Verification (матрица, тест-план, checker) | `qa-01` | `planner-01` | `critic-01` |

Правила: security-строки — два ревьюера; QA не ревьюит свой `TEST_PLAN` без второго подписанта; Planner не ревьюит собственные решения; генератор — только `planner-01`.

---

## 14. Что проверяется автоматически, что — только вручную

**Автоматически:** покрытие WBS↔матрица↔тест-план, генератор, наличие/уникальность ID, зависимости, `pytest --collect-only`, replay red/green, coverage по diff, gitleaks, cookie-флаги, state/nonce replay, claim rate limit, `pair_secret`, IDOR/CSRF, редакция журнала, ротация/чтение, миграция/rollback на фикстуре, флаг `MESH_ACCOUNT=0`, mode × scopes, i18n-паритет, checker mutation.

**Только вручную/на железе:** реальный Google-вход, автозапуск после перезагрузки, иконка/меню/тосты трея, Windows ACL, single-file publish без админа, SSE-реконнект после сна, captive portal, DPI 125/150%, UX-приёмка. Для них нужны `M-01…M-08` с оператором, машиной, скриншотами до/после и числовым порогом; сейчас все 11 TRY-строк `Blocking=yes` без артефакта — главный риск «зелёного без доказательства».
