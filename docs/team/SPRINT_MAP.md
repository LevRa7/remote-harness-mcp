# Карта спринтов Wave 1

**Правило:** спринт не закрывается, пока все его WBS-строки не имеют `APPROVED` в `REVIEW_LOG.md` и свежих артефактов. Полный список — в [WBS.md](WBS.md), владельцы — в [OWNERSHIP_MATRIX.md](OWNERSHIP_MATRIX.md).

## Sprint 0 — фундамент и hotfix (неделя 1)

| Блок | WBS | Владелец | Выход |
| :--- | :--- | :--- | :--- |
| Каркас кабинета | AUTH-01, REV-ARC-01, REV-OPS-12, REV-OPS-13 | `be-auth` + `ops-01` | `/account` монтируется, миграции/бэкап/откат, ADR |
| Тестовый каркас | REV-QA-01, REV-QA-02 | `qa-01` | `conftest`, fake OIDC, coverage/JUnit |
| Безопасность немедленно | REV-SEC-13, REV-SEC-14, REV-SEC-15 | `be-data` + `sec-01` | read-only guard, node server auth, registry hash |
| Данные | REV-CAB-15, REV-PERF-01 | `api-01` + `be-data` | конверт ошибок, SQLite async policy |
| Порядок | REV-ARC-02 | `arch-01` | policy-слой объявлен до UI-тумблера |

**Гейт:** `checker --strict` = 0, тесты каркаса зелёные, hotfix-тесты красные до и зелёные после.

## Sprint 1 — вход и привязка (недели 2–3)

| Блок | WBS | Владелец | Выход |
| :--- | :--- | :--- | :--- |
| OIDC и сессии | AUTH-02…AUTH-18, REV-AUTH-19…21 | `be-auth`, rev `sec-01` | Реальный Google-вход, cookie, CSRF, step-up |
| Claim | CLM-01…CLM-10, REV-CLM-11…14 | `be-auth`, rev `sec-01`/`api-01` | `pair_secret`, preview/confirm, CLI, unlink |
| Защита | REV-SEC-09…12 | `sec-01` | reserved names, CORS, trusted proxies, audit |
| Журнал (старт) | REV-USE-17 | `be-data` | единый путь и ACL |

**Гейт:** сквозной сценарий «Google → код на устройстве → кабинет»; claim-гонка; реальный Google — M-01.

## Sprint 2 — кабинет, агенты, журнал (недели 4–5)

| Блок | WBS | Владелец | Выход |
| :--- | :--- | :--- | :--- |
| Кабинет | CAB-01…CAB-14, REV-CAB-15…16 | `ui-cab`, rev `api-01` | Устройства, агенты, статистика, права |
| Агенты/ключи | AGT-01…AGT-13, REV-AGT-11…13 | `be-auth`, rev `sec-01` | `ak_`, отзыв только своего агента, legacy default |
| Журнал/ingest | USE-01…USE-18, REV-USE-15/16/18 | `be-data`, rev `qa-01` | JSONL, CSV, агрегаты, auth ingest |
| Права | MOD-01…MOD-08, REV-MOD-09/10 | `be-data`, rev `sec-01` | policy до UI, `call_id` |
| API/ops | REV-OPS-10/11, REV-DOC-01 | `api-01`/`doc-01` | serverInfo, privacy |

**Гейт:** кабинет на 2 аккаунтах, IDOR, отзыв агента, CSV, статистика ±1% (или контракт сверки).

## Sprint 3 — трей и меню (недели 6–7)

| Блок | WBS | Владелец | Выход |
| :--- | :--- | :--- | :--- |
| Трей | TRY-01…TRY-11 | `desk-tray`, rev `ui-cab` | Иконка, меню, журнал, автозапуск |
| Состояния | REV-UI-01…09 | `ui-cab` | unclaimed/agent_down/paused, i18n, a11y, DPI |
| Control API | CTL-01…CTL-10, REV-CTL-11 | `be-data`/`desk-tray` | `control.json`, Host/Origin, SSE |
| Нагрузка | REV-PERF-06 | `perf-01` | 100 WS, 20 вызовов, p95 |

**Гейт:** ручные M-02/M-05/M-06; живой туннель; 100/125/150% DPI.

## Неделя 8 — стабилизация

- Финальный whole-branch review (не ревьюил ни одной задачи).
- Пентест-раунд по критичным сценариям, исправления High.
- Документация, changelog, runbook, откат.
- `checker --strict`, coverage-пороги, `evidence.json`, подписи.

---

## Кто за что отвечает (сводка)

| Роль | Агент | Спринты | Ключевые артефакты |
| :--- | :--- | :--- | :--- |
| Planner | `planner-01` | 0–3 | WBS, карта, rulings, ledger |
| Architect | `arch-01` | 0–3 | ADR-004/005/006, `BridgeHooks` |
| Critic | `critic-01` | 0–3 | findings, pre-mortem, финальное ревью |
| API | `api-01` | 0–2 | OpenAPI, коды, contract-тесты |
| Security | `sec-01` | 0–3 | threat model, OIDC/claim, security-гейты |
| Backend auth | `be-auth` | 0–2 | `account/`, claim, agents |
| Backend data | `be-data` | 0–3 | schema, usage, policy, control API |
| UI | `ui-cab` | 2–3 | кабинет, состояния, a11y |
| Desktop | `desk-tray` | 3 | Avalonia-трей, автозапуск |
| Perf | `perf-01` | 0–3 | SQLite/SSE/бенчи |
| QA | `qa-01` | 0–8 | TEST_PLAN, фикстуры, гейты |
| Ops | `ops-01` | 0–8 | CI, деплой, бэкап, откат |
| Docs | `doc-01` | 2–8 | README, privacy, runbook |
