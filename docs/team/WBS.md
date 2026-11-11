# WBS Wave 1 — декомпозиция до деталей

**Область:** только Wave 1 (авторизация, кабинет, локальный журнал, меню настроек).
**Правило:** каждая строка имеет ровно одну запись в `OWNERSHIP_MATRIX.md` и минимум один тест в `TEST_PLAN.md`.
**Проверка:** `python tools/check_team_coverage.py --strict`.

Обозначения зависимостей: `—` нет, иначе ID, который должен быть закрыт раньше.

---

## AUTH — вход и сессии

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| AUTH-01 | Конфиг OIDC и валидация на старте | Без `client_id`/`secret`/`base_url` кабинет отдаёт «не настроен», MCP работает; в логе нет секретов | — |
| AUTH-02 | `/account/auth/google/start` | Генерируются `state` (32 B), `nonce` (32 B), PKCE S256; state привязан к cookie; редирект на Google с точными scopes | AUTH-01 |
| AUTH-03 | Безопасный `next` | Принимается только относительный путь из allowlist; `//evil`, `https://evil`, `/account//x` отклоняются | AUTH-02 |
| AUTH-04 | Callback и обмен code | Code меняется на токен по TLS с таймаутом; ошибки Google маппятся в понятную страницу; повторный code отклоняется | AUTH-02 |
| AUTH-05 | JWKS: подпись и ротация | Подпись RS256 проверяется; неизвестный `kid` вызывает одно обновление JWKS; кэш с TTL; ключи не логируются | AUTH-04 |
| AUTH-06 | Claims ID token | Проверяются `iss`, `aud`, `exp`, `iat`, `azp`; допуск часов ≤ 60 с; просроченный/чужой токен отклоняется | AUTH-05 |
| AUTH-07 | Nonce | Nonce из ID token совпадает с сохранённым, используется один раз, истекает вместе с login-сессией | AUTH-02, AUTH-06 |
| AUTH-08 | Email | Требуется `email_verified=true`; email нормализуется (lowercase, trim), ограничение длины; невалидный отклоняется | AUTH-06 |
| AUTH-09 | Upsert аккаунта | Поиск по `google_sub`; повторный вход не создаёт дубль; смена email обновляет запись | AUTH-08 |
| AUTH-10 | Личный тенант | Ровно один личный тенант на аккаунт; создание идемпотентно; чужой тенант не переиспользуется | AUTH-09 |
| AUTH-11 | Создание сессии | Сессия — 32 B random, хранится в БД, id ротируется при входе; старая сессия невалидна | AUTH-10 |
| AUTH-12 | Cookie сессии | `__Host-rhm_session`: Secure, HttpOnly, SameSite=Lax, Path=/, без Domain, корректный Max-Age | AUTH-11 |
| AUTH-13 | TTL и продление | Idle 7 дней, absolute 30 дней; `last_seen` обновляется; просроченная сессия отклоняется и удаляется | AUTH-11 |
| AUTH-14 | Выход | Серверная сессия удаляется, cookie очищается, повторный logout идемпотентен; «выйти везде» удаляет все сессии | AUTH-11 |
| AUTH-15 | CSRF и Origin | CSRF-токен привязан к сессии, сравнение постоянное по времени; все POST проверяют токен и same-origin | AUTH-11 |
| AUTH-16 | Rate limit и lockout | Лимиты по IP и аккаунту; 429 с `Retry-After`; после N ошибок — временная блокировка; ответы не раскрывают существование аккаунта | AUTH-04 |
| AUTH-17 | Dev-режим | Включается только явным флагом; работает только с localhost; при не-localhost отказ; баннер «dev» | AUTH-01 |
| AUTH-18 | Удаление аккаунта | Требует повторного ввода email; каскадно удаляет сессии/агентов; устройства отвязываются и отзываются; аудит; отмена в grace-период | AUTH-10, CLM-06 |

## CLM — привязка устройства

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| CLM-01 | Старт pairing с токеном и без | С валидным device token код привязывается к существующему узлу; без токена создаётся заявка, устройство появляется только после claim | AUTH-01 |
| CLM-02 | Формат и TTL кода | 8 символов из алфавита без похожих, `XXXX-XXXX`, TTL 10 минут, код не угадывается (≥ 40 бит энтропии) | CLM-01 |
| CLM-03 | Одноразовость и гонка | Код используется один раз; два параллельных claim дают ровно одну привязку; повтор — 409/400 без деталей | CLM-02 |
| CLM-04 | Rate limit claim | Лимит по IP и аккаунту; перебор кода не влияет на валидные заявки; 429 с `Retry-After` | CLM-02 |
| CLM-05 | Claim только с сессией | Без валидной сессии — 401; CSRF обязателен; чужой tenant не может подтвердить код | AUTH-12, CLM-04 |
| CLM-06 | Привязка к тенанту | После claim `devices.tenant_id`, `registry[node].device_id/tenant_id` согласованы; транзакция атомарна | CLM-05 |
| CLM-07 | Наследование старых узлов | Узел, зарегистрированный до v2, привязывается без переустановки и без смены device token | CLM-06 |
| CLM-08 | Уже привязанное устройство | Повторный claim другим аккаунтом отклоняется; владелец видит устройство; аудит попытки | CLM-06 |
| CLM-09 | Аудит и анти-энумерация | Пишутся `claim_started/succeeded/failed`; ответы не отличают «нет кода» от «чужой код» | CLM-03 |
| CLM-10 | Статус для трея | Устройство может узнать «ожидает подтверждения / привязано / отклонено» по коду без раскрытия данных аккаунта | CLM-01 |

## CAB — кабинет

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| CAB-01 | Дашборд | Онлайн-устройства, число агентов, вызовы за 24 ч, алерты; данные совпадают с БД; запрос < 200 мс на 100 устройствах | CLM-06 |
| CAB-02 | Список устройств | Таблица с именем, ОС, версией, статусом, последним контактом, числом агентов; поиск и фильтр по статусу | CAB-01 |
| CAB-03 | Карточка устройства | Поля и действия: rename, revoke, агенты, usage, опасная зона; 404 для чужого | CAB-02 |
| CAB-04 | Переименование | Trim, 1–64 символа, Unicode, запрет управления; XSS-экранирование; аудит; конфликт имён обрабатывается | CAB-03 |
| CAB-05 | Отзыв устройства | Подтверждение вводом имени; устройство отключается, ключи отзываются, данные помечаются; повтор идемпотентен | CAB-03 |
| CAB-06 | Список агентов | Клиент, устройство, режим, scopes, last_used, статус; фильтры; отозванные скрыты по умолчанию | CAB-02 |
| CAB-07 | Редактирование агента | Меняются label, mode, scopes, workspace; валидация; изменения применяются к новым вызовам; аудит | CAB-06, MOD-01 |
| CAB-08 | Отзыв агента | Подтверждение; ключ и сессии отзываются; другие агенты устройства работают; следующий вызов 401 | CAB-06, AGT-08 |
| CAB-09 | Usage-фильтры | Период, устройство, агент, tool, статус; пагинация; суммы совпадают с агрегатами | CAB-01 |
| CAB-10 | Usage-график | Вызовы по дням, топ-5 инструментов, ошибки/отказы; пустые дни нулями; нет payload | CAB-09 |
| CAB-11 | Настройки | Профиль, «делиться статистикой», удаление аккаунта; изменения аудируются; 404 для чужого | CAB-01 |
| CAB-12 | Пагинация и сортировка | Cursor-пагинация, стабильный порядок, нет дублей/пропусков при вставке | CAB-02 |
| CAB-13 | Состояния | Loading/empty/error/offline/degraded/нет прав; ошибки человеческим текстом с кодом | CAB-02 |
| CAB-14 | i18n, a11y, адаптив | ru/en, даты/числа по локали, клавиатура, контраст AA, aria-live, мобильный viewport | CAB-13 |

## AGT — агенты и ключи

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| AGT-01 | Создание агента на initialize | Первый `initialize` создаёт `AgentConnection`; повторный — обновляет `last_used`, не дублирует | CLM-06 |
| AGT-02 | Метка клиента | `clientInfo.name` нормализуется в label (≤ 64), неизвестный → «Unknown client»; XSS-безопасно | AGT-01 |
| AGT-03 | Генерация ключа | 32 B random, префикс `ak_`, высокая энтропия; ключ не повторяется | AGT-01 |
| AGT-04 | Хранение ключа | В БД только SHA-256; сравнение постоянное по времени; plaintext нигде не логируется | AGT-03 |
| AGT-05 | Выдача ключа | URL показывается один раз, копируется, после закрытия невосстановим; в БД — хеш | AGT-04 |
| AGT-06 | Порядок разрешения токена | Agent-key → legacy link_key → device token; неоднозначность отклоняется; результат содержит `agent_id` | AGT-03 |
| AGT-07 | Миграция legacy link_key | Существующий ключ становится агентом `default`; старые URL работают; отзыв default не ломает других | AGT-06 |
| AGT-08 | Отзыв агента | Ключ помечается revoked, активные сессии закрываются, in-flight вызовы завершаются по политике; аудит | AGT-06 |
| AGT-09 | Режим, scopes, workspace | Дефолт `read_only`; scopes из allowlist; workspace валиден; некорректные значения → 400 | AGT-01, MOD-01 |
| AGT-10 | Лимиты и аудит | Не более N агентов на устройство; события create/update/revoke в аудите; last_used обновляется | AGT-09 |

## USE — локальный журнал

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| USE-01 | Схема JSONL | Версия, обязательные поля, типы; неизвестные поля игнорируются; документировано | — |
| USE-02 | Запись | Append одной строкой, flush по политике; при сбое питания теряется максимум последняя строка | USE-01 |
| USE-03 | Права файла | 0600 (POSIX) / ACL только пользователя (Windows); проверка на старте | USE-01 |
| USE-04 | Ротация и gzip | 10 МБ × 5, старые `.gz`, ротация не блокирует вызовы, имена сортируемы | USE-02 |
| USE-05 | Ретенция | 90 дней по умолчанию, настраивается; очистка не удаляет активный файл; лог операции | USE-04 |
| USE-06 | Повреждённая строка | Чтение пропускает битую строку, пишет warning, не падает; сводка не врёт | USE-02 |
| USE-07 | Сводка: counts | ok/error/denied/cancelled, по агентам и инструментам; суммы совпадают с числом строк | USE-06 |
| USE-08 | Сводка: перцентили | p50/p95 по duration_ms, корректны на 1 и 100k событий; без загрузки всего файла в память | USE-07 |
| USE-09 | Фильтры и CSV | Фильтры tool/agent/status/period; CSV с заголовком, экранированием, UTF-8 BOM для Excel | USE-07 |
| USE-10 | Очистка | `DELETE /v1/usage` и CLI `--clear`; подтверждение; файл пересоздаётся с 0600 | USE-03 |
| USE-11 | Отсутствие секретов | Сканер: в журнале нет аргументов, путей, вывода, токенов; тест на `cat secret`, `--password` | USE-01 |
| USE-12 | Время | Все ts в UTC ISO-8601 с мс; отображение в локали; монотонность при переводе часов | USE-01 |
| USE-13 | Outbox | При офлайне агрегаты в очередь; повтор при подключении; ограничение размера; нет дублей | USE-07 |
| USE-14 | Ingest | `POST /account/api/usage/ingest` идемпотентен по `(device,agent,day,tool)`; auth device token; отравление отклоняется | USE-13, AUTH-01 |

## CTL — control API

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| CTL-01 | Привязка | Только loopback/Unix-socket/named pipe; случайный порт; нет `0.0.0.0`; проверка на старте | — |
| CTL-02 | Токен | 32 B random, файл 0600/ACL, генерируется при первом старте, не логируется | CTL-01 |
| CTL-03 | Отказ без прав | Нет токена/чужой токен/не-localhost → 401/403; не раскрывает состояние | CTL-02 |
| CTL-04 | State | Схема: версия, шлюз, статус, режим, папка, агенты, active_calls, approvals; стабильные имена | CTL-02 |
| CTL-05 | SSE | События `state/call/approval/error`; heartbeat; реконнект без потери последнего состояния; медленный клиент не блокирует агент | CTL-04 |
| CTL-06 | Settings PATCH | Валидация `mode/workspace/paused/autostart/pulse`; частичное обновление; ошибки 400 с полем | CTL-04 |
| CTL-07 | Пауза | `paused=true` → новые вызовы `node_paused`; текущие завершаются; снятие возвращает работу; аудит | CTL-06 |
| CTL-08 | Usage endpoints | `GET /v1/usage`, `/summary`, `/export.csv`; лимиты, пагинация, 404 на неизвестный файл | USE-09 |
| CTL-09 | Single instance и shutdown | Второй экземпляр не поднимает второй сервер; корректное закрытие сокета/файла; без осиротевших процессов | CTL-01 |
| CTL-10 | Версия и изоляция | `/v1/version` без секретов; API недоступен из сети; нет DNS rebinding (Host/Origin) | CTL-03 |

## TRY — трей и меню

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| TRY-01 | Запуск и single instance | Один экземпляр; повторный запуск открывает меню; падение пишет лог; нет админ-прав | CTL-09 |
| TRY-02 | Состояния иконки | offline/idle/active/approval/error; смена < 200 мс; tooltip с версией и статусом | TRY-01, CTL-05 |
| TRY-03 | Меню | Пункты и порядок из плана; disabled при недоступности; локализация; клавиатура | TRY-02 |
| TRY-04 | Режим по умолчанию | Переключение с подтверждением; применяется к новым агентам; сохраняется после рестарта | TRY-03, CTL-06 |
| TRY-05 | Рабочая папка | Нативный диалог; валидация; сохранение; отображение; сброс | TRY-03, MOD-07 |
| TRY-06 | Окно журнала | Таблица, фильтры, пагинация, пустое состояние; 10k строк без фризов; обновление | TRY-03, CTL-08 |
| TRY-07 | Экспорт из трея | CSV сохраняется, диалог выбора пути, ошибка прав обрабатывается, файл открывается | TRY-06 |
| TRY-08 | Автозапуск включение | Запись HKCU Run; фактическая проверка; путь к exe корректен; без админа | TRY-01 |
| TRY-09 | Автозапуск выключение | Запись удаляется; после перезагрузки трей не стартует; состояние UI совпадает | TRY-08 |
| TRY-10 | Уведомления и кабинет | Тост при подключении агента; «Открыть кабинет» ведёт на правильный URL; ошибка авторизации видна | TRY-03 |
| TRY-11 | Упаковка, DPI, i18n | Single-file publish; 100/125/150% DPI; ru/en; подпись/предупреждение; crash-лог | TRY-01 |

## MOD — режимы, scopes, workspace

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| MOD-01 | SessionContext | Контекст `mode/scopes/agent_id/workspace` на вызов; нет глобального протекания между агентами | AGT-09 |
| MOD-02 | Приоритет MESH_READ_ONLY | Глобальный флаг отключает мутации независимо от scopes; тест на все мутирующие инструменты | MOD-01 |
| MOD-03 | Фильтрация tools/list | Read-only видит только разрешённые; scopes влияют на список; старый клиент не ломается | MOD-01 |
| MOD-04 | Отказ tools/call | Стабильный код `mode_denied`/`scope_denied`, человеческий текст, без утечки политики | MOD-03 |
| MOD-05 | Принуждение на узле | Прямой вызов узла с мутацией в read-only отклоняется; обход через `run_job`/`job_kill` закрыт | MOD-01 |
| MOD-06 | Принуждение на мосту | Мост фильтрует до отправки; старый узел без ctx получает дефолт; аудит отказов | MOD-04 |
| MOD-07 | Workspace | Абсолютный путь, существует, каталог; обход `..`, symlink, junction, UNC запрещён; cwd для bash/run_job; missing → `workspace_unavailable` | MOD-01 |
| MOD-08 | Понижение режима | Новые вызовы отклоняются; активные мутирующие джобы по политике; событие в аудите | MOD-05, CAB-07 |

## SEC — сквозная безопасность

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| SEC-01 | Session fixation | Новый session id после логина; старый невалиден; cookie не принимает чужой id | AUTH-11 |
| SEC-02 | Утечка токенов | Токены/ключи/секреты отсутствуют в логах, URL, Referer, CSV, ошибках; сканер проходит | AGT-04, USE-11 |
| SEC-03 | SQL-инъекции | Все запросы параметризованы; тесты с `' OR 1=1`, `; DROP`, Unicode | CAB-02 |
| SEC-04 | XSS и CSP | Автоэкранирование; CSP без `unsafe-inline`; тест с `<script>` в имени/метке | CAB-04, AGT-02 |
| SEC-05 | CSRF | Все мутации требуют токен; cross-origin POST отклоняется; GET не мутирует | AUTH-15 |
| SEC-06 | IDOR | Чужие device/agent/share → 404; проверка tenant на каждом запросе; тест с двумя аккаунтами | CAB-03, CAB-08 |
| SEC-07 | Rate limit и абьюз | Логин, claim, ingest, API кабинета лимитированы; 429; нет user enumeration | AUTH-16, CLM-04 |
| SEC-08 | Секреты и зависимости | `MESH_SESSION_SECRET` ≥ 32 B, отказ при слабом; pip-audit/bandit чисто или waivers | AUTH-01 |

## OPS — эксплуатация и совместимость

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| OPS-01 | Миграции | Up/down идемпотентны; `schema_version`; откат на копии данных; нет потери | — |
| OPS-02 | Бэкап/восстановление | SQLite backup API; restore на чистую машину; проверка целостности; RPO/RTO задокументированы | OPS-01 |
| OPS-03 | Флаг выключен | `MESH_ACCOUNT=0` → поведение 0.4.8; старые маршруты и тесты зелёные | AUTH-01 |
| OPS-04 | Совместимость | Старый узел 0.4.x подключается, `tools/list` работает, link-key действителен; matrix-тест | OPS-03 |
| OPS-05 | CI-матрица | Python 3.11 (floor) и 3.13 (current), Ubuntu/Windows, unit+contract; < 10 мин; flaky = fail | OPS-01 |
| OPS-06 | Деплой | `deploy_gateway.sh` ставит зависимости кабинета; healthcheck; откат на предыдущий релиз | OPS-05 |
| OPS-07 | Runbook и откат | Пошаговый runbook: логин не работает, БД повреждена, откат; проверен на стенде | OPS-02, OPS-06 |
| OPS-08 | Мониторинг и нагрузка | Метрики auth failures/5xx/DB; алерты; smoke 100 устройств/10k событий; p95 в бюджете | OPS-06 |

---

## REV — доработки по итогам ревью №1

> Эти строки добавлены после отчётов Architect/API/Security/UI/QA. Они не удаляют исходные 111 строк и закрывают найденные слепые зоны.
> `REV-RESCUE-01…04` — отдельный LiveUSB-проект ([LIVEUSB_SEPARATION.md](LIVEUSB_SEPARATION.md)); оставлены для трассировки и не входят в релиз Wave 1.

| ID | Работа | Критерий приёмки | Зависит от |
| :--- | :--- | :--- | :--- |
| REV-CLM-11 | `pair_secret` и fingerprint | `POST /v1/devices/pair` только с device token; ответ содержит `pair_secret` (32 B) и fingerprint; `GET` без секрета → 404; claim не выдаёт ключи | CLM-01 |
| REV-CLM-12 | Единый state machine claim | Статусы `pending/claimed/expired`; preview показывает имя/ОС/fingerprint; confirm атомарен; гонка даёт одну привязку; поллинг 2–5 с без данных аккаунта | REV-CLM-11 |
| REV-CLM-13 | Unlink и удаление аккаунта | Unlink снимает `tenant_id`, узел продолжает работать; удаление аккаунта чистит БД, оставляет audit-tombstone, не сохраняет доступ | REV-CLM-12, AUTH-18 |
| REV-AUTH-19 | Base URL, redirect, ошибки Google | Base по умолчанию = `PUBLIC_BASE_URL`; расхождение роняет старт; redirect точный; отказ Google → `303 /account/login?e=<code>`; таймауты 5/10 с | AUTH-01 |
| REV-AUTH-20 | Серверный store auth-flow и step-up | `state/nonce/code_verifier/next` в БД, TTL 10 мин, одноразово; step-up ≤ 10 мин для claim/revoke/delete; аудит | AUTH-02, AUTH-15 |
| REV-AGT-11 | Контракт ключа агента | `ak_` + 43 base64url; sha256; единый resolver; без параметра `agent=`; `lk_` → default; revoked → `401 agent_revoked`; чужой `user=` → 404 | AGT-03, AGT-06 |
| REV-AGT-12 | Потоки, привязанные к агенту | `active_sessions`/SSE индексируются `(user, agent_id)`; отзыв A закрывает только A; тест на живом туннеле | AGT-08 |
| REV-USE-15 | Контракт usage API | `since/until` RFC 3339; `limit` 1–1000; cursor; `status` enum; ответ `{items,next_cursor}`; CSV `text/csv` + BOM + `Content-Disposition`; DELETE 200 с counts | USE-09, CTL-08 |
| REV-USE-16 | Auth и лимиты ingest | Только device token; `device_id` из аутентификации; проверка владения `agent_id`; день UTC в окне; ≤ 500 бакетов, ≤ 256 КБ; merge по max | USE-14 |
| REV-CAB-15 | Единый конверт ошибок | RFC 9457 `problem+json`; MCP `isError{code,retryable,request_id}`; реестр ~20 кодов; golden-тест на каждый | API-04 |
| REV-CAB-16 | Пагинация, идемпотентность, кеш | `limit` 1–100; opaque cursor; sort allowlist; `If-Match` → 412; `Idempotency-Key` 24 ч; `no-store`, `Vary: Cookie` | REV-CAB-15 |
| REV-CTL-11 | Обнаружение control API | Атомарный `control.json` `{pid,port,proto,version,started_at}`; tray ретраи/backoff; очистка stale; Host/Origin allowlist; JSON-only | CTL-01 |
| REV-OPS-09 | `hello` и старые узлы | `hello` опционален; без него `proto=1`; при `MESH_ACCOUNT=1` мутации старого узла → `policy_unavailable`; golden `tools/list` | MOD-06 |
| REV-OPS-10 | `serverInfo` | Реальная версия; `listChanged:true` либо документированный re-init; тест | AUTH-01 |
| REV-OPS-11 | Namespace кабинета | Wave 1 — только `/account/api/*`; `/v1/account/*` зарезервирован за Wave 3; документы не противоречат | CAB-01 |
| REV-SEC-09 | Reserved names, mount, CORS | `account/v1/pair/privacy/login/logout` зарезервированы; `/account` монтируется до catch-all; wildcard CORS не действует на `/account`; `Vary: Origin` | AUTH-01 |
| REV-SEC-10 | Доверенные прокси и лимиты | `X-Forwarded-For` только от trusted CIDR; лимиты `(account, ip, device)`; общий store или явно «1 процесс» | AUTH-16 |
| REV-SEC-11 | Read-confine и секреты | Read-инструменты ограничены `read_roots`; deny `.ssh/.aws/.gnupg/agent.env/*.pem/id_*`; тесты | MOD-03 |
| REV-SEC-12 | Аудит и admin-token | `prev_hash`, actor, request_id, append-only; подмена обнаруживается; `MESH_ADMIN_TOKEN` отключён/скоуплен при `MESH_ACCOUNT=1` | SEC-05 |
| REV-SEC-13 | Немедленный hotfix read-only | Guard в `bash_exec`, `run_job`, `unshare`; allowlist имён инструментов; `_http_share` только внутренним каналом | MOD-05 |
| REV-SEC-14 | Защита локального MCP-сервера | Токен обязателен всегда; Origin/Host allowlist; JSON-only; нет `ACAO:*` на мутациях | CTL-03 |
| REV-SEC-15 | Реестр и link-key | `sha256(link_key)` + prefix; 0600/DACL; атомарная запись + lock; fail-closed при порче; миграция | AGT-04 |
| REV-UI-01 | Жизненный цикл узла и трея | Владелец автозапуска один; «Скрыть значок» ≠ «Остановить узел»; тумблер снимает Startup и watchdog; составное состояние | TRY-01, TRY-08 |
| REV-UI-02 | Матрица состояний | `unclaimed/agent_down/paused/update_required/version_mismatch`; `approval` убран из Wave 1; состояние → иконка/меню/tooltip | TRY-02 |
| REV-UI-03 | Панель первого запуска | Код, TTL-таймер, «Обновить», ошибки 429/истёк, ссылка «где ввести код» | TRY-03, CLM-10 |
| REV-UI-04 | CSV formula injection | Экранируются `= + - @ TAB CR`; тест `=cmd|' /c calc'!A0`; числа не ломаются | USE-09 |
| REV-UI-05 | Контракт i18n | Общие ключи трея/кабинета; плюрализация ru; `html lang`; даты/числа; тест паритета | CAB-14 |
| REV-UI-06 | Доступность | WCAG 2.2 AA чек-лист; axe 0 critical; focus trap/Esc; `aria-sort`; NVDA вручную | CAB-14 |
| REV-UI-07 | Тема, DPI, иконки | Light/dark/HC токены; контраст измерен; иконки 16–48; 100–200% и два монитора | TRY-11 |
| REV-UI-08 | Уведомления | AUMID/ярлык; fallback balloon/бейдж; проверка Win10/11 | TRY-10 |
| REV-UI-09 | Время и пагинация журнала | UTC в файле; локальное отображение с TZ; DST-тест; cursor-пагинация без дублей | USE-12, TRY-06 |
| REV-DOC-01 | Privacy-страница | Перечислено, что видит шлюз (метаданные) и что не видит (args/пути/вывод); сверено с перехватом | OPS-07 |
| REV-PERF-01 | SQLite в async-процессе | Все обращения через `asyncio.to_thread`, thread-local соединения, `PRAGMA WAL/busy_timeout/synchronous=NORMAL`, `BEGIN IMMEDIATE` для миграций; event loop не блокируется | REV-CAB-15 |
| REV-PERF-02 | Ретенция и перцентили | TTL для usage/audit/sessions; каскад при удалении аккаунта; p50/p95 хранятся как гистограмма/`duration_ms_max`, а не только `sum` | REV-USE-15 |
| REV-PERF-03 | Семантика ingest | Явно: дельта или накопительный итог; `updated_at`; `day` не в будущем и не старше retention; клампы счётчиков; повтор не откатывает данные | REV-USE-16 |
| REV-PERF-04 | Backpressure | `maxsize` SSE-очередей, медленный клиент отбрасывается по политике; пул потоков узла ≥ числа параллельных вызовов; тест 100 потоков | CTL-05 |
| REV-PERF-05 | Журнал и Windows | Политика fsync/батчинга, ротация при открытом читателе (WinError 32) через rename/retry, стоимость append на p95 | USE-04 |
| REV-PERF-06 | Нагрузочные гейты | Стенд 2 vCPU/4 ГБ; 100 WS, 20 вызовов, 1 млн строк, 10k событий/день; p95 API < 200 мс, p99 < 500 мс, tray RSS ≤ 120 МБ, старт ≤ 3 с; `bench.json` и EXPLAIN-гейт | OPS-08 |
| REV-QA-01 | Тестовый каркас | `conftest.py`, app factory, DI-часы, сетевой гард, per-test БД; запрет `.clear()` глобалов и зависимости от порядка | QA-01 |
| REV-QA-02 | Coverage и CI-гейты | `pytest-cov` branch, `diff-cover`, `--junitxml`, upload-artifact, отдельный job с зависимостями кабинета; пороги в G4/G5 | OPS-05 |
| REV-QA-03 | Chaos и миграции | Failpoint после каждого DDL, `kill -9`, `SQLITE_BUSY`, `ENOSPC`, up/down/restore, сверка схемы и отсутствие частичных данных | OPS-01 |
| REV-QA-04 | Flaky и маркеры | `pytest-timeout`, `--strict-markers`, `flaky/serial/slow/manual`, 3× прогон blocking, карантин ≤ 10 дней | VERIFICATION |
| REV-QA-05 | Манифест доказательств | `evidence.json` (commit, cmd, exit, sha256, reviewer), машинная проверка, retention и upload | VERIFICATION |
| REV-QA-06 | Security-оракулы | Матрица «угроза → тест → точное ожидание»: rate limit, IDOR, CSP, constant-time, replay-окно, fuzz аргументов | SEC-05 |
| REV-AGT-13 | Режим legacy-агента при апгрейде | Существующий `lk_` становится default-агентом в режиме `agent` (сохраняет поведение), показывается предупреждение и кнопка ограничить; новые агенты — `read_only` | AGT-07 |
| REV-OPS-12 | Деплой account-компонентов | `deploy_gateway.sh` доставляет `account/`, шаблоны, static, зависимости, создаёт каталог БД 0600, применяет миграции, healthcheck | OPS-06 |
| REV-OPS-13 | Откат Wave 1 | Предыдущий релиз + бэкап БД, downgrade схемы и `MESH_ACCOUNT=0`; откат проверен на копии данных, описано восстановление | OPS-02 |
| REV-CLM-14 | Headless-привязка | `remote-harness-mcp pair` печатает код и TTL, сохраняет состояние, работает без трея, поллит статус и завершает привязку | CLM-10 |
| REV-AUTH-21 | Google `iss`/`aud` | Уникальность (`iss`,`sub`), хранение `aud`/`client_id`, сценарий слияния аккаунтов задокументирован; тест двух клиентов | AUTH-09 |
| REV-MOD-09 | Parity `tools/list` | Фильтрация до `annotate_tools`; parity-тест сравнивает полный набор с `core/mcp_tools.TOOLS`; read-only видит N, agent — M | MOD-03 |
| REV-MOD-10 | Идемпотентность вызова | `call_id` в кадре, LRU-дедуп 5 мин на узле, одна запись в журнале, retry с тем же id | MOD-01 |
| REV-USE-17 | Путь журнала | Единственный путь через `core/paths.config_dir()`/`MESH_STATE_DIR`; трей и служба видят один файл; ACL | USE-03 |
| REV-USE-18 | Статусы и байты | Enum включает `lost/unknown`; `bytes` берётся с узла; `denied_at_bridge` учитывается отдельно | USE-07 |
| REV-OPS-14 | nginx для кабинета | `location /account` с `proxy_buffering off`, лимитом тела и `limit_req`; regex `*.json` не блокирует i18n/JSON кабинета | OPS-06 |
| REV-OPS-15 | Vendor/selfhost guard | `MESH_DEPLOYMENT=vendor|selfhost`; на selfhost account-слой не монтируется и не читает Google-секреты | REV-SEC-09 |
| REV-ARC-01 | ADR и BridgeHooks | ADR-004/005/006 в WBS; протокол `BridgeHooks` (≥ 7 методов) зафиксирован; тест отсутствия циклического импорта | AUTH-01 |
| REV-ARC-02 | Порядок принуждения | Policy-слой (MOD-01…06) внедрён до переключателя режима в кабинете (CAB-07); до этого переключатель скрыт/disabled и помечен «не применяется» | MOD-01 |
| REV-RESCUE-01 | LiveUSB: `rescue_info` | Read-only диагностика (disks/smart/partitions/raid/installed_os/boot_efi/win_artifacts/dmesg_io), таймауты, защита от D-state I/O hang, фикстуры `lsblk -J`/`smartctl -j`; в read-only режиме доступна | REV-SEC-13 |
| REV-RESCUE-02 | LiveUSB: `rescue_action` | `dry_run=True` по умолчанию, destructive-аннотация, `MESH_READ_ONLY`/tray read-only отказывают, блокировка образов в `/tmp`, bind-маунты и chroot только с подтверждением | REV-SEC-13 |
| REV-RESCUE-03 | LiveUSB: firstboot и сборка | TUI на tty1: сеть, QR, active-uplink lock; `build-iso.sh` с firmware/утилитами; без сторонних runtime-зависимостей; образ не пишет на диск клиента | REV-RESCUE-01 |
| REV-RESCUE-04 | LiveUSB: интеграция и упаковка | `rescue_info`/`rescue_action` в `TOOLS`/`_HANDLERS`/`TOOL_ANNOTATIONS`, gateway parity, `core/rescue.py` и `ops/live-usb/` в bootstrap/payload, `tests/test_rescue.py` | REV-RESCUE-02 |
| REV-MENU-01 | `rhm`: лог, ссылка, QR | Команда `rhm` (python/npm/Windows launcher) показывает хвост `agent.log`, MCP URL из `MESH_LINK_KEY`, QR; при отсутствии ключа — ротация через gateway или понятная ошибка | REV-SEC-14 |
| REV-MENU-02 | `rhm`: три режима и approvals | Tab/Left/Right: `read_only` / `approval` / `full`; в approval мутирующий вызов даёт `approval_required`, меню `a`/`x`; grant одноразовый | REV-ARC-02 |
| REV-MENU-03 | `rhm`: рабочая директория | `d` выбирает существующий каталог, `workspace.txt` 0600, агент применяет к следующему вызову без рестарта | REV-MENU-01 |
| REV-MENU-04 | `rhm`: терминалы и QR | Windows (`msvcrt`, ANSI) и POSIX (`termios`) клавиши; UTF-8; fallback без `qrcode`; `--json`/`--once` для скриптов | REV-MENU-01 |
| REV-SANDBOX-01 | Изолированный контейнер | `i` в `rhm`: Docker/Podman, `.rhm-sandbox/` в проекте, build/start/stop, состояние в control; без Docker — понятная ошибка | REV-MENU-01 |
| REV-SANDBOX-02 | Shell/jobs в контейнере | `bash_exec`/`run_job` выполняются через `docker exec` в `/workspace`; нет доступа к хостовой ФС, capabilities dropped, PID/memory limits | REV-SANDBOX-01 |
| REV-SANDBOX-03 | Конфайнмент файлов | Абсолютные пути вне проекта отклоняются, `/workspace/...` маппится на проект; меню показывает ON/OFF и рабочую папку | REV-SANDBOX-02 |
| REV-SANDBOX-04 | Strict/network и runtime | `--network none` по умолчанию, `I` с подтверждением, gVisor/Kata при наличии, лимиты PID/RAM/CPU, запрет system-root workspace | REV-SANDBOX-01 |
| REV-WEB-01 | Веб-настройки | `rhm --web`: loopback, token+CSRF, те же режимы/папка/isolation/approvals/лог/QR; мутации через `core.settings` | REV-MENU-01 |
| REV-DRIVE-01 | Per-user Google Drive OAuth | `/account/drive/connect`: `drive.file`, `access_type=offline`, state/nonce/PKCE; refresh-token per account, шифрование at rest | AUTH-01 |
| REV-DRIVE-02 | Cabinet Drive view | `/account/drive`: список только app-visible файлов, disconnect, статус; ошибки без утечки токена | REV-DRIVE-01 |

---

**Итого:** 183 строки WBS (111 исходных + 72 REV). Изменения после следующих ревью добавляются с новыми `REV-` ID и не удаляют существующие строки.
