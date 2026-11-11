# Google OAuth для Remote Harness MCP

Две разные авторизации, их легко перепутать:

| Что | Кто входит | Что нужно |
| :--- | :--- | :--- |
| **Кабинет** (`/account`) | человек | Google OAuth 2.0 Client ID + consent screen |
| **MCP-клиент** (Claude/ChatGPT) | облачный агент | OAuth 2.1 authorization server **на нашем шлюзе** (Wave 2) или link-key URL (уже работает) |

Google Cloud — только upstream-провайдер личности. Он не выдаёт токен, который MCP-клиент может предъявить нашему серверу: audience у Google-токена — Google, а не ваш MCP. Поэтому для MCP нужен либо link-key, либо собственный OAuth-сервер, который доверяет Google-логину.

---

## 0. Для текущего Racknerd-хоста (192.129.148.93)

Проверено по SSH: хост `racknerd-5a24bf9`, публичный домен **`smart-server.online`**
(резолвится в `192.129.148.93`), TLS-сертификат действует, шлюз отвечает
`https://smart-server.online/health` → `200`, `/sse` → `401` (значит, это шлюз).

Поэтому для этого сервера:

```dotenv
MESH_ACCOUNT_BASE_URL=https://smart-server.online
```

Redirect URI в Google Console:

```
https://smart-server.online/account/auth/google/callback
```

Не используйте `accounts.smart-server.online`: этот vhost намеренно отдаёт
`410 Gone` после инцидента с фишинговым зеркалом (см. конфиг nginx
`gemini-accounts-gone.smart-server.online`). `racknerd-5a24bf9.merino-carob.ts.net`
— Tailscale-адрес `100.x`, серверы Google до него не достучатся, для OAuth он не
подходит.

Сейчас `/account` отдаёт `307` (маршрута ещё нет); после Wave 1 он будет
смонтирован до catch-all и начнёт отвечать.

---

## 1. Что сделать в Google Cloud

1. Создать проект (или выбрать существующий).
2. **APIs & Services → OAuth consent screen**:
   - User type: **External** (для личного проекта) или **Internal** (Google Workspace);
   - App name, support email, developer contact;
   - Scopes: `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/userinfo.profile` — этого достаточно для входа. Для Drive добавьте **`https://www.googleapis.com/auth/drive.file`** (только файлы, созданные/открытые приложением; весь Drive не открывается).
   - После выбора обязательно нажать **Update** (и **Save**). Если в блоках
     «Your non-sensitive scopes» слева пусто — scopes **не сохранены**, и Google
     вернёт `invalid_scope`;
   - Для публичного доступа — **Publish app** (для `openid/email/profile` обычно без верификации);
   - Пока приложение в **Testing**, добавить себя (и тестовых пользователей) в **Test users**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**;
   - Name: `Remote Harness MCP`;
   - **Authorized JavaScript origins**: `https://<ваш-домен>` (и `http://localhost:<port>` для локальной разработки);
   - **Authorized redirect URIs**: `https://<ваш-домен>/account/auth/google/callback` — **побайтово**, без лишнего слэша;
   - Скопировать **Client ID** и **Client secret**.

> «Cloud OAuth Service API» включать не обязательно: стандартному OAuth 2.0 client ID не нужен отдельный API. Достаточно consent screen + OAuth client. Если включили — не мешает.

---

## 2. Переменные окружения шлюза

```dotenv
MESH_ACCOUNT=1
MESH_ACCOUNT_BASE_URL=https://<ваш-домен>
MESH_GOOGLE_CLIENT_ID=<...>.apps.googleusercontent.com
MESH_GOOGLE_CLIENT_SECRET=<...>
MESH_SESSION_SECRET=<32+ случайных байта, например openssl rand -hex 32>
MESH_TOKEN_KEY=<32+ случайных байта>   # шифрование refresh-token Drive; иначе derive из session secret
```

- `MESH_ACCOUNT_BASE_URL` должен совпадать с доменом, на который зарегистрирован redirect URI; при расхождении — `redirect_uri_mismatch`.
- Секреты хранить в `0600` env-файле, не в git.
- `MESH_SESSION_SECRET` — не пароль пользователя, а ключ подписи cookie; менять его = разлогинить всех.
- `MESH_TOKEN_KEY` — отдельный ключ для токенов Drive; при потере нужно переподключить Drive.

**Текущее состояние кода:** кабинет и `/account/auth/google/*` реализованы; `/account/drive/connect` запрашивает `drive.file`, токены хранятся per-account в зашифрованном виде. MCP-инструменты Drive — следующий слой (нужно связать device/link-key с аккаунтом).

---

## 3. Как это будет работать для кабинета

```
GET /account/auth/google/start
  → state + nonce + PKCE, cookie
  → redirect на accounts.google.com
GET /account/auth/google/callback?code&state
  → обмен code на id_token (TLS, timeout)
  → проверка подписи JWKS, iss/aud/exp/nonce/email_verified
  → аккаунт + сессия (cookie __Host-rhm_session)
```

Проверки, которые обязательны: `RS256`, `iss=https://accounts.google.com`, `aud=client_id`, `azp`, `exp/iat` ±60 с, `nonce` одноразовый, `email_verified=true`, JWKS с кэшем и fail-closed.

---

## 4. Что нужно для MCP-клиента

### Вариант A — link-key (работает сейчас)

```
https://<домен>/sse?user=<node>&token=lk_<...>
```

`rhm` показывает этот адрес и QR. Ссылка = пароль: не публиковать, при утечке — `POST /v1/devices/<node>/rotate`.

### Вариант B — OAuth 2.1 (Claude, ChatGPT; Wave 2)

MCP-клиент придет на `/sse`, получит `401` с `WWW-Authenticate`, прочитает `/.well-known/oauth-protected-resource`, зарегистрируется через DCR, откроет `/authorize`, где **наш** шлюз проведёт Google-логин и покажет consent. На выходе — токен с `aud=<наш MCP>`, а не Google-токен.

Что понадобится:

- authorization server metadata (`/.well-known/oauth-authorization-server`);
- DCR (`/register`), PKCE, consent, token/refresh/revoke;
- Google OAuth client из §1 как upstream;
- проверка токена на шлюзе (JWKS/подпись, scopes, agent/device).

Gemini Connectors сегодня чаще работают по link-key; для них Вариант A остаётся основным.

---

## 5. Чек-лист проверки

- [ ] Redirect URI в Google Console совпадает с `MESH_ACCOUNT_BASE_URL/account/auth/google/callback`.
- [ ] `MESH_ACCOUNT=1`, `client_id`, `client_secret`, `session_secret` заданы.
- [ ] Приложение в Testing, ваш аккаунт — в Test users (или app опубликован).
- [ ] HTTPS на домене; cookie `Secure`, `HttpOnly`, `SameSite=Lax`.
- [ ] После логина `/account` открывается, logout удаляет сессию.
- [ ] `rhm` показывает URL и QR; link-key не попал в git/логи.

Типовые ошибки: `redirect_uri_mismatch` — другой домен/слэш/порт; `invalid_client` — неверный secret; `access_denied` — пользователь не в Test users или отказал в consent; `invalid_scope` — в Data Access не отмечен `openid` (или изменения не нажаты **Update**).

Если Google показывает `Error 400`, посмотрите параметры в адресной строке:
`error=invalid_scope` → добавьте `openid` в Data Access и нажмите **Update**;
`error=redirect_uri_mismatch` → сверьте redirect URI в Credentials побайтово;
`error=invalid_client` → перевыпустите secret и обновите `MESH_GOOGLE_CLIENT_SECRET`.
