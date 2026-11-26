# Дизайн: быстрый слой данных для Windows и обход лимита вызовов MCP (`batch_read`)

- **Дата:** 2026-11-26
- **Статус:** на утверждении
- **Запрос:** устранить таймауты при сканировании дисков на Windows (`D:\`, профиль пользователя), ускорить взаимодействие с файлами/документами на машине и снизить расход лимита вызовов инструментов за один ход AI-клиента.
- **Целевые компоненты:**
  - `core/fs_search.py` (новый) — быстрый `os.scandir()`-обходчик с ранним prune, бюджетом времени и опциональными внешними бэкендами (`es.exe`, `fd`, `rg`)
  - `core/fs_index.py` (новый) — лёгкий локальный SQLite FTS5 индекс файлов в `paths.cache_path("file_index.db")`
  - `core/platforms/windows/places.py` (новый) + `core/platforms/windows/winapi.py` — Known Folders, `.lnk`-парсер `Recent` и обзор дисков за 1 вызов
  - `core/doc_read.py` (новый) — stdlib-чтение `.docx`/`.xlsx`/`.pptx`/`.odt` для `read_media`
  - `core/mcp_tools.py` — перенос `glob_find`/`grep_search` на `fs_search`, добавление `win_places` и мультиплексора `batch_read`

---

## 1. Диагностика реальных узких мест

### 1.1. Почему скан диска `D:\` уходит в таймаут шлюза (~28 с)
1. **Баг `_tool_glob_find` (`core/mcp_tools.py:2584-2592`):** при наличии `/` или `**` в шаблоне вызывается `glob.glob(..., recursive=True)`, который обходит **всё дерево без фильтрации** (включая `node_modules`, `.git`, `.venv`, `.cache`, `.bun`, `$RECYCLE.BIN`), и только после полного обхода фильтрует `_skip_path(p)`.
2. **Узкий `_SKIP_DIRS` (`core/mcp_tools.py:233`):** содержит только 7 папок (`.git`, `.hg`, `.svn`, `node_modules`, `__pycache__`, `.venv`, `venv`). На Windows остаются тяжёлые деревья: `$RECYCLE.BIN`, `System Volume Information`, `WinSxS`, `AppData\Local\Temp`, `.cache`, `.bun`, `.npm`, `.cargo`, `.gradle`, `site-packages`, `dist`, `build`.
3. **Нет `time_budget_ms` и частичной выдачи:** ни `glob_find`, ни `grep_search` (`core/mcp_tools.py:2480-2560`) не контролируют wall-clock бюджет. При превышении ~25–28 с шлюз обрывает запрос (`DEADLINE_EXCEEDED`), и все уже найденные совпадения теряются.
4. **PowerShell `Get-ChildItem -Recurse` через `bash_exec`:** создаёт тяжёлые .NET-объекты `FileSystemInfo` на каждый файл и упирается в тот же 25-секундный таймаут.
5. **Отсутствие Windows-слоя быстрого контекста:** в `core/platforms/windows/` есть только `clipboard.py`, `process.py`, `screen.py`, `telemetry.py`, `winapi.py`. Чтобы найти «моё резюме» или «последний документ», модель вынуждена делать серию слепых обходов дисков вместо одного чтения Known Folders + `Recent`.

### 1.2. Почему заканчивается лимит вызовов инструментов за один ход
- **Честная граница:** лимит числа последовательных шагов / tool calls в одном ходе задаётся на стороне AI-клиента (Gemini Web, ChatGPT и т.д.). Узел и `gateway.py` не могут увеличить этот лимит напрямую.
- **Архитектурный фикс на стороне MCP:** сократить число вызовов в **5–10 раз** за счёт:
  1. **Мультиплексора `batch_read`** — 1 MCP-вызов выполняет до 16 read-only операций (`list_dir`, `read_file`, `glob_find`, `grep_search`, `win_places`, `mesh_status`, `system_vitals`) в рамках одного ответа и одного `time_budget_ms`.
  2. **Составного инструмента `win_places`** — 1 вызов возвращает реальные пути `Desktop`/`Documents`/`Downloads`/`OneDrive`, топ-30 последних открытых файлов из `Recent` и список дисков с 1-м уровнем папок.

---

## 2. Цели и инварианты

1. **Zero third-party dependencies:** весь функционал на чистой Windows работает на Python 3.8+ stdlib (`os.scandir`, `sqlite3`, `zipfile`, `xml.etree.ElementTree`, `ctypes`).
2. **Никаких таймаутов на поиске:** любой вызов `glob_find`, `grep_search`, `batch_read` гарантированно завершается внутри `time_budget_ms` (дефолт 12 000 мс, потолок 20 000 мс — с запасом до 28-секундного порога шлюза) и отдаёт `partial: true` + статистику обхода.
3. **Совместимость с `MESH_READ_ONLY=1`:** все новые инструменты (`win_places`, `batch_read`, индексный поиск) входят в `_READ_ONLY_ALLOWED` (`core/mcp_tools.py:488`).
4. **Безопасность на Windows:** защита от циклов NTFS junction / symlink (`FILE_ATTRIBUTE_REPARSE_POINT`), отсутствие необработанных исключений (ошибки доступа `PermissionError` тихо считаются в `skipped_errors`, не роняя обход).

---

## 3. Архитектура по модулям

### 3.1. `core/fs_search.py` — быстрый обходчик + бюджет времени
- **Полная замена `glob.glob(..., recursive=True)`:** единый итеративный стек поверх `os.scandir()` (BFS по уровням глубины — сначала находит файлы ближе к корню, а не тонет в одной глубокой ветке).
- **Ранний prune:** до спуска в подкаталог проверяется:
  - совпадение имени (case-insensitive на Windows) с расширенным `_SKIP_DIRS_DEFAULT` (включая `$RECYCLE.BIN`, `System Volume Information`, `Windows`, `WinSxS`, `.cache`, `.bun`, `.npm`, `.pytest_cache`, `node_modules`, `.git`, `.venv`, `venv`, `__pycache__`);
  - флаг `include_ignored: bool = False` для явного отключения prune;
  - `entry.is_symlink()` / reparse point (не входить в циклы).
- **Контроль бюджета:** на каждые 128 записей проверяется `time.monotonic() >= deadline` и `len(results) >= max_results`. При исчерпании возвращается:
  ```json
  {
    "files": ["..."],
    "count": 42,
    "truncated": true,
    "partial": true,
    "backend": "scandir",
    "elapsed_ms": 12004,
    "scanned_dirs": 1840,
    "pruned_dirs": 95,
    "next_hint": "Narrow path or set max_depth / use fs_index"
  }
  ```
- **Опциональные ускорители (без обязательной установки):**
  - для `glob_find`: если на хосте найден `es.exe` (Everything CLI) и запущена служба Everything — запрос <50 мс; иначе локальный `fs_index` (если свежий); иначе `scandir` с бюджетом;
  - для `grep_search`: если найден `rg.exe` / `rg` — делегировать с `--json` и `--max-count`; иначе `scandir` + пофайловое чтение с тем же `time_budget_ms` и лимитом размера файла (`max_file_bytes=2MB`).

### 3.2. `core/fs_index.py` — фоновый локальный индекс (SQLite FTS5)
- **Хранилище:** `paths.cache_path("file_index.db")`, WAL-режим (`PRAGMA journal_mode=WAL`), минимальное потребление RAM.
- **Схема:**
  - таблица `files(path TEXT PRIMARY KEY, name TEXT, ext TEXT, size INTEGER, mtime REAL, is_dir INTEGER, root TEXT)`
  - виртуальная таблица `files_fts USING fts5(name, path, content='files', content_rowid='rowid', tokenize='unicode61')`
- **Что индексирует по умолчанию на Windows:**
  - `USERPROFILE` (кроме `AppData\Local\Temp`, кэшей браузеров, `.bun`, `.cache`, `node_modules`)
  - рабочие корни из `MESH_INDEX_ROOTS` (или `D:\MyProjects` / верхние 4 уровня фиксированных дисков)
- **Обновление:**
  - лёгкий фоновый поток-демон с низким приоритетом (паузы между батчами, чтобы не грузить диск);
  - инкрементальный перескан по `mtime` каталогов каждые N минут + явный триггер `index_refresh` при необходимости;
  - в `glob_find` параметр `use_index: "auto" | "only" | "off"` (дефолт `"auto"`).
- **Скорость:** поиск `*резюме*` или `*resume*` по всему `C:\` + `D:\` занимает **10–30 мс** вместо >30 с.

### 3.3. `core/platforms/windows/places.py` — инструмент `win_places`
Один read-only вызов (≤100 мс) возвращает структурированный снимок машины:
1. **Known Folders** через `shell32.SHGetKnownFolderPath` + `ole32.CoTaskMemFree` (добавить прототипы в `core/platforms/windows/winapi.py`): `Desktop`, `Documents`, `Downloads`, `Pictures`, `OneDrive`, плюс проверка `%USERPROFILE%\Downloads\Telegram Desktop`.
2. **Recent Files (`%APPDATA%\Microsoft\Windows\Recent`):**
   - чистый Python-парсер бинарного формата MS-SHLLINK (`.lnk`, ~70 строк stdlib без COM/pywin32): читает `LinkInfo` / `LocalBasePath` / `UnicodeLocalBasePath` и `mtime` ярлыка;
   - возвращает последние `limit` (дефолт 30) реально существующих файлов с путями.
3. **Диски и верхний уровень:** `GetLogicalDrives` + `GetDriveTypeW` + `GetDiskFreeSpaceExW` (уже есть в `winapi.py:173-178`) + список папок 1-го уровня на каждом фиксированном диске.

### 3.4. `core/doc_read.py` — чтение офисных документов без зависимостей
- Интеграция в `read_media` (`core/mcp_tools.py`):
  - `.docx` → `zipfile` → `word/document.xml` → извлечение параграфов и таблиц;
  - `.xlsx` → `zipfile` → `xl/sharedStrings.xml` + `xl/worksheets/sheet*.xml` → компактный CSV/текст по листам;
  - `.pptx` → `zipfile` → `ppt/slides/slide*.xml` → текст слайдов;
  - `.odt` → `zipfile` → `content.xml`.
- Поддержка пагинации через существующие `offset` и `max_chars` в `read_media`.

### 3.5. `batch_read` в `core/mcp_tools.py` — экономия лимита вызовов в 5–10×
- **Сигнатура:**
  ```json
  {
    "ops": [
      {"id": "p", "tool": "win_places", "args": {"recent_limit": 20}},
      {"id": "f1", "tool": "glob_find", "args": {"path": "D:\\", "pattern": "*resume*", "time_budget_ms": 5000}},
      {"id": "r1", "tool": "read_file", "args": {"path": "ProjectPlan.md", "start_line": 1, "end_line": 80}}
    ],
    "time_budget_ms": 18000,
    "max_total_chars": 40000
  }
  ```
- **Ограничения и безопасность:**
  - Белый список строго read-only инструментов: `mesh_status`, `system_vitals`, `list_dir`, `read_file`, `grep_search`, `glob_find`, `win_places`, `job_list`, `job_output` (с `wait_ms=0`).
  - Не более 16 операций в одном батче; запрещена рекурсия (`batch_read` внутри `batch_read`).
  - Общий `time_budget_ms` (потолок 20 000 мс) и общий лимит текста `max_total_chars` (чтобы не раздуть контекст клиента).
  - Независимые операции выполняются в `ThreadPoolExecutor(max_workers=4)`, каждая изолирована `try/except`, в ответе возвращается `{"results": [{"id": ..., "ok": true/false, "data": ...}], "elapsed_ms": ..., "completed": N, "total": M}`.

---

## 4. Критерии готовности (Acceptance Criteria)

1. `glob_find(path="D:\\", pattern="**/*resume*")` на `MateBook16` завершается за **≤ 12 с** (без таймаута шлюза), а при прогретом `fs_index` — за **≤ 0.2 с**.
2. `win_places()` возвращает `known_folders`, `recent` (реальные целевые пути из `.lnk`) и `drives` за **≤ 0.3 с**.
3. `batch_read` с 8 разнородными операциями отрабатывает за **1 MCP-вызов** и укладывается в `time_budget_ms`.
4. Весь новый код покрыт unit-тестами (`pytest`), проходящими и на Linux, и на Windows (`py -3 -m pytest`).
5. Ни одной новой внешней pip-зависимости.
