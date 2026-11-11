# Команда и контроль качества Wave 1

Этот каталог — «операционный штаб» первого выпуска: кто за что отвечает, как доказывается работоспособность и где смотреть результаты ревью.

| Файл | Назначение |
| :--- | :--- |
| [AGENT_TEAM.md](AGENT_TEAM.md) | Устав: роли, RACI, эскалация, Definition of Done |
| [REVIEW_BRIEF.md](REVIEW_BRIEF.md) | Бриф, по которому работают ревью-агенты |
| [WBS.md](WBS.md) | 183 детали Wave 1 (111 исходных + 72 REV, включая LiveUSB, rhm-меню, sandbox, web и Drive) с критериями приёмки |
| [SPRINT_MAP.md](SPRINT_MAP.md) | Какая строка в каком спринте, кто ведёт, какой гейт |
| [LIVEUSB_SEPARATION.md](LIVEUSB_SEPARATION.md) | LiveUSB выносится в отдельный проект; граница с harness |
| [TASK_BRIEF_TEMPLATE.md](TASK_BRIEF_TEMPLATE.md) | Шаблон task brief для Superpowers (Files/Interfaces/Red/Green/commit) |
| [briefs/AUTH-01.md](briefs/AUTH-01.md) | Пример заполненного brief |
| [OWNERSHIP_MATRIX.md](OWNERSHIP_MATRIX.md) | Деталь → владелец → ревьюер → тесты → доказательство |
| [TEST_PLAN.md](TEST_PLAN.md) | Тест-кейсы: шаги, ожидания, автоматизация, окружение |
| [VERIFICATION_PROTOCOL.md](VERIFICATION_PROTOCOL.md) | Железное правило «доказательство до заявления» |
| [REVIEW_LOG.md](REVIEW_LOG.md) | Findings, rulings, подписи |
| [reviews/](reviews/) | Полные отчёты ревью-агентов |

## Проверка покрытия

```bash
python tools/check_team_coverage.py --strict
```

Скрипт проверяет, что у каждой строки WBS есть владелец, ревьюер, минимум один блокирующий тест, а каждый тест ссылается на существующую деталь. Ненулевой код возврата = нельзя объявлять спринт закрытым.

> **Важно:** чекер проверяет **план** (наличие владельца/теста/ссылок), а не выполнение тестов. Зелёный чекер не означает, что код работает: G5 дополнительно требует свежие прогоны, `evidence.json` и подписи в `REVIEW_LOG.md`.

## Порядок работы

1. Planner добавляет деталь в `WBS.md`.
2. Matrix-builder/Planner закрепляет владельца и ревьюера в `OWNERSHIP_MATRIX.md`.
3. QA добавляет тест в `TEST_PLAN.md`.
4. Владелец проходит Red → Green → Evidence.
5. Ревьюер проверяет и подписывает `REVIEW_LOG.md`.
6. `check_team_coverage.py --strict` + CI-гейты.
