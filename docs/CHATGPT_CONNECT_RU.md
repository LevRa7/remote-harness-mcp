# Подключение Remote Harness MCP к ChatGPT

Инструкция для страницы **ChatGPT → Plugins → Add custom MCP server**.
Рассчитана на узел `matebook16` и шлюз `racknerd-5a24bf9.merino-carob.ts.net`;
для другого узла подставьте своё имя и свой `lk_…`.

## 0. Что понадобится

- узел онлайн: агент подключён к шлюзу, `agent.heartbeat` свежий;
- **link key** вида `lk_…` — его показывает `rhm` в строке `Token:`, он же лежит в
  `%USERPROFILE%\.config\antigravity-mash\agent.env` в `MESH_LINK_KEY`;
- URL для ChatGPT собирается **не** как `/sse`, а как `/mcp`:

```text
https://racknerd-5a24bf9.merino-carob.ts.net/mcp?user=matebook16&token=lk_…
```

`/sse` — транспорт для Gemini; ChatGPT подключается по Streamable HTTP, поэтому
ему нужен `/mcp`. Токен устройства (`32 hex`) в URL не подставляйте: шлюз
принимает его только в заголовке `Authorization`, а в ссылке — только `lk_…`.

## 1. Откройте диалог

1. Перейдите на <https://chatgpt.com/plugins?directoryTab=personal>.
2. Нажмите **Add** справа сверху.
3. Выберите **Add custom MCP server**.

## 2. Заполните поля

| Поле в ChatGPT | Что вписать |
| :--- | :--- |
| **Icon (optional)** | можно пропустить. Если нужна иконка — PNG 256×256, до 10 КБ. |
| **Name** | `MateBook16` (или `Remote Harness MCP`). Имя видно в списке плагинов. |
| **Description (optional)** | `Удалённое управление ПК MateBook16: файлы, команды, задания.` |
| **Connection** | **Server URL** (не `Tunnel`). |
| **Server URL** | `https://racknerd-5a24bf9.merino-carob.ts.net/mcp?user=matebook16&token=lk_…` |
| **Authentication** | **No authentication / None**, если такой пункт есть. Ссылка уже содержит `lk_…`, и шлюз проверяет её на каждом запросе. Если в списке только `OAuth` — см. §3. |
| **Advanced OAuth settings** | не трогать, если выбран `No authentication`. |
| **I understand and want to continue** | поставить галочку. |

Поле **Server URL** должно начинаться с `https://` и содержать **и** `user=`,
**и** `token=lk_…`. Если ChatGPT обрежет query-параметры, подключение не
пройдёт: тогда проверьте, что ссылка вставлена целиком, одной строкой.

## 3. Authentication: важная оговорка

В текущем шлюзе **нет полноценного OAuth 2.1** для MCP-клиентов:

- `/.well-known/oauth-protected-resource` отдаётся, но
  `authorization_servers` в нём пустой;
- `/authorize`, `/register` и `/token` не реализованы — это Wave 2
  ([docs/GOOGLE_OAUTH.md](GOOGLE_OAUTH.md)).

Поэтому:

- если в **Authentication** есть **No authentication / None** — выбирайте его,
  всё работает по link key из URL;
- если доступен **только OAuth** — ChatGPT попытается пройти OAuth-дискавери и
  не сможет: authorization server некуда идти. Подключение из этого диалога не
  заработает, пока не появится OAuth-сервер на шлюзе. На сегодня надёжный путь
  для такого аккаунта — **Gemini** по `/sse?...&token=lk_…`, либо ждать OAuth.

Не вписывайте в OAuth-поля Google Client ID/secret: это авторизация кабинета,
а не MCP-клиента, и ChatGPT не сможет предъявить Google-токен шлюзу.

## 4. Создайте плагин

1. Поставьте галочку **I understand and want to continue**.
2. Нажмите **Create as a plugin**.
3. Дождитесь, пока ChatGPT прочитает `initialize` и `tools/list`. Если список
   инструментов пуст — см. §6.

## 5. Проверка

1. Откройте новый чат.
2. Включите созданный плагин (в интерфейсе ChatGPT он появляется в списке
   плагинов/инструментов).
3. Попросите:

```text
Вызови mesh_status и покажи node_version, hostname и workspace.
```

Ожидаемый ответ:

```text
hostname: MateBook16
node_version: 0.6.0
workspace: D:\MyProjects
```

Можно проверить и чтение файлов:

```text
Покажи список файлов в D:\MyProjects через list_dir.
```

Если оба вызова отвечают — плагин подключён к вашему ПК, а не к песочнице
ChatGPT.

## 6. Если не работает

| Симптом | Причина и что делать |
| :--- | :--- |
| `401 Unauthorized` | В URL нет `lk_…`, ключ старый/ротирован или вставлен токен устройства. Возьмите текущий `MESH_LINK_KEY` из `rhm`/`agent.env` и пересоздайте ссылку. |
| `404` | Неверный путь или имя узла. Для ChatGPT — `/mcp?user=matebook16`, не `/sse`. |
| OAuth-ошибка, `authorization server` | Выбран `OAuth`, а шлюз его не предоставляет. Выберите `No authentication`; если такого пункта нет — см. §3. |
| Плагин создан, но инструментов нет | Проверьте, что узел онлайн (`agent.heartbeat`, `Connected to Mesh Gateway` в `agent.log`) и что шлюз отвечает на `initialize` по `/mcp`. |
| Инструменты есть, но вызов падает | Проверьте режим узла: `rhm` → `Access`. В `read_only`/`approval` часть вызовов требует подтверждения. |
| Ссылка работала, потом перестала | Link key перевыпустили (`rotate`). Возьмите новый и обновите URL плагина. |

Диагностика с ПК:

```powershell
# токен и URL
$p = "$env:USERPROFILE\.config\antigravity-mash\agent.env"
(Select-String -Path $p -Pattern '^MESH_LINK_KEY=').Line -replace '^MESH_LINK_KEY=',''
```

Проверка, что шлюз видит узел, — вызов `mesh_status` через тот же `/mcp`;
если он возвращает `node_version`, транспорт и ключ в порядке.

## 7. Безопасность

- Ссылка с `lk_…` — это пароль: любой, у кого она есть, может вызывать
  инструменты на ПК. Не публикуйте её и не вставляйте в чужие чаты.
- ChatGPT получает **все** инструменты узла, которые разрешены текущим режимом.
  Для первого подключения разумно поставить `rhm mode read_only` или
  `approval`, а потом расширять.
- При утечке ссылки перевыпустите link key:

```text
POST /v1/devices/matebook16/rotate
Authorization: Bearer <device token>
```

Старый URL сразу перестанет работать; новый нужно вставить в ChatGPT заново.

## Короткая версия для страницы `/connect`

Блок под карточкой ChatGPT:

> **ChatGPT**
> 1. Откройте <https://chatgpt.com/plugins?directoryTab=personal> → **Add** →
>    **Add custom MCP server**.
> 2. **Name:** `MateBook16`.
> 3. **Connection:** `Server URL`, вставьте ссылку:
>    `https://racknerd-5a24bf9.merino-carob.ts.net/mcp?user=matebook16&token=lk_…`
> 4. **Authentication:** `No authentication` (ссылка уже содержит ключ).
> 5. Поставьте галочку риска и нажмите **Create as a plugin**.
> 6. В новом чате попросите `mesh_status` — должен вернуться `node_version`.
>
> Если в **Authentication** нет `No authentication`, а только `OAuth` —
> подключение из ChatGPT сейчас невозможно: шлюзу нужен OAuth 2.1 (Wave 2).
