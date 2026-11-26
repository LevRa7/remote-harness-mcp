# План реализации: Windows Fast Data Layer & `batch_read`

- **Спецификация:** `docs/superpowers/specs/2026-11-26-windows-fast-data-layer-and-batching.md`
- **Дата:** 2026-11-26
- **Принцип:** только Python 3.8+ stdlib + `ctypes` Win32 API; каждый этап завершается прогоном `py -3 -m pytest`.

---

## Этап 1: Починить `glob_find` и `grep_search` (`core/fs_search.py`) — приоритет P0
1. Создать `core/fs_search.py`:
   - BFS-обходчик на `os.scandir()` с ранним prune (`_SKIP_DIRS_DEFAULT` + Windows-специфичные мусорные директории + защита от symlink/junction).
   - Поддержка `time_budget_ms` (дефолт 12000, макс 20000), `max_depth`, `max_results`, `include_ignored`.
   - Честный возврат `partial: true`, `elapsed_ms`, `scanned_dirs`, `pruned_dirs`, `backend`.
   - Опциональные fast-path адаптеры для `es.exe` (Everything) и `rg` (ripgrep), если они есть в `PATH`.
2. Перевести `_tool_glob_find` и `_tool_grep_search` в `core/mcp_tools.py` на `core/fs_search.py`, полностью убрав `_glob.glob(..., recursive=True)`.
3. Добавить `tests/test_fs_search.py` (prune, `**`-паттерны, `max_depth`, срабатывание `time_budget_ms` с `partial=True`, symlink-цикл).

## Этап 2: Добавить мультиплексор `batch_read` (`core/mcp_tools.py`) — приоритет P0
1. Реализовать `_tool_batch_read(args)` с белым списком read-only инструментов (`mesh_status`, `system_vitals`, `list_dir`, `read_file`, `grep_search`, `glob_find`, `win_places`, `job_list`, `job_output`).
2. Добавить лимиты: максимум 16 операций, `time_budget_ms` (до 20000 мс), `max_total_chars` (дефолт 40000), выполнение через `ThreadPoolExecutor(max_workers=4)`.
3. Зарегистрировать `batch_read` в `_READ_ONLY_ALLOWED`, `TOOL_ANNOTATIONS`, схеме `tools/list` и диспетчере.
4. Обновить `skills/orchestrator.md` / `get_orchestration_skill`: рекомендовать `batch_read` для группировки чтений/проверок в 1 вызов.
5. Добавить `tests/test_batch_read.py` (запрет write-инструментов, запрет рекурсии, частичное завершение по `time_budget_ms`, обрезка по `max_total_chars`).

## Этап 3: Windows-слой быстрого контекста (`win_places`) — приоритет P1
1. В `core/platforms/windows/winapi.py` добавить прототипы `shell32.SHGetKnownFolderPath` и `ole32.CoTaskMemFree`.
2. Создать `core/platforms/windows/places.py`:
   - `known_folders()` (Desktop, Documents, Downloads, Pictures, OneDrive, Telegram Desktop);
   - `parse_lnk(path)` (чистый Python MS-SHLLINK парсер) + `recent_files(limit=30)`;
   - `drives_overview()` (логические диски, свободное место, папки 1-го уровня).
3. Подключить инструмент `win_places` в `core/mcp_tools.py` (на не-Windows возвращает кроссплатформенный fallback: `HOME`, `Desktop`/`Documents`/`Downloads`, точки монтирования).
4. Добавить `tests/test_win_places.py` (включая фикстуру бинарного `.lnk`-файла для кроссплатформенного прогона на Linux/Windows).

## Этап 4: Локальный фоновый индекс SQLite FTS5 (`core/fs_index.py`) — приоритет P1
1. Создать `core/fs_index.py` (WAL SQLite + FTS5 в `paths.cache_path("file_index.db")`).
2. Реализовать фоновый индексатор с низким I/O-приоритетом, инкрементальным обновлением по `mtime` и фильтрацией через `_SKIP_DIRS_DEFAULT`.
3. Подключить в `glob_find` параметр `use_index` (`auto`/`only`/`off`).
4. Добавить `tests/test_fs_index.py`.

## Этап 5: Stdlib-чтение офисных документов (`core/doc_read.py`) — приоритет P2
1. Создать `core/doc_read.py` для `.docx`, `.xlsx`, `.pptx`, `.odt` на `zipfile` + `xml.etree.ElementTree`.
2. Подключить в `_tool_read_media` (`core/mcp_tools.py`) с поддержкой `offset` и `max_chars`.
3. Добавить `tests/test_doc_read.py` с синтетическими ZIP/XML фикстурами.
