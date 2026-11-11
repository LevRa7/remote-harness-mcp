# Task brief — шаблон

> Заполняется Planner'ом на каждую WBS-строку перед dispatch. Brief — единственный источник требований; implementer не читает весь план.

**WBS:** `<ID>`
**Owner:** `<role/agent>` · **Reviewer:** `<role/agent>`
**Test:** `<T-ID>` · **Gate:** `<G0..G5>`
**Зависимости:** `<ID...>`

## Files

- Create: `<точный путь>`
- Modify: `<точный путь>:<строки>`
- Test: `<точный путь>::<test_name>`

## Interfaces

- Consumes: `<сигнатуры из предыдущих задач>`
- Produces: `<точные имена, параметры, типы>`

## Step 1 — failing test

```python
# код теста с точными значениями из WBS/спеки
```

## Step 2 — verify red

```bash
<команда>
# Expected: FAIL — <точный текст>
```

## Step 3 — implement

`<файл>`: `<сигнатура>`; тело — по тесту. Решения, которые нельзя вывести из теста: `<...>`.

## Step 4 — verify green

```bash
<команда>
# Expected: PASS
```

## Step 5 — commit

```bash
git add <файлы>
git commit -m "<тип>(<область>): <что>"
```

## Evidence

- `artifacts/<WBS>/red.log`, `artifacts/<WBS>/green.log`
- Reviewer проверяет: red-green, негативные кейсы, отсутствие секретов, соответствие WBS.

## Definition of Done

- [ ] Тест падал до реализации (red.log).
- [ ] Тест проходит (green.log), регресс зелёный.
- [ ] Ревьюер `APPROVED` в `REVIEW_LOG.md`.
- [ ] Нет открытых Blocker/Critical по строке.
