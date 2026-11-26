# Reddit: полный текст поста (Remote Harness MCP)

Готово к вставке. Шапка и заголовки — для вас, в пост идёт только блок «Body».
Ассеты: `docs/reddit/demo-preview.mp4`, `cover-1600x900.png` (RU: `-ru`).

---

## Заголовок (выберите один)

1. Google keeps putting Gemini 4 behind smaller doors — so I built a different door: control your PC from the Gemini app (open source MCP)
2. Antigravity will not launch for me and Gemini 4 keeps vanishing from AI Pro — so I gave the regular Gemini app hands (MIT)
3. They put the best models inside the app that will not open for you. So I made the app itself the agent.
4. "Your account is not eligible" → my phone now runs `git pull` on my home server through Gemini
5. Your AI Pro does not guarantee the models anymore. Here is a Gemini MCP server that only needs the chat.

---

## Body

**Google shipped four models and a shorter list of people allowed to touch them.**

Quick recap of the last few weeks, in case you were busy touching grass:

- Sept 30 — Ars Technica: ["Google announces Gemini 4 Argon AI model, but you can't use it yet"](https://arstechnica.com/google/2026/09/google-announces-gemini-4-argon-ai-model-but-you-cant-use-it-yet/). A month later Argon starts rolling out to the Gemini app and Antigravity — ["but only if you pay"](https://arstechnica.com/ai/2026/10/gemini-4-argon-begins-rolling-out-in-gemini-app-and-antigravity-but-only-if-you-pay/).
- Nov 23 — 9to5Google: ["Gemini 4 Carbon is now available in Google Antigravity for AI Pro"](https://9to5google.com/2026/11/23/antigravity-gemini-4-carbon/). Great news. It lives inside the app.
- Also November — if your AI Pro arrived as a Jio bundle, the Antigravity model picker just shrugs: Argon, Neon, Carbon, Hydrogen, all marked "Unavailable", on a paid plan. ["Google AI Pro (Jio bundle): All Gemini 4 models (including Hydrogen & Neon) marked Unavailable in Antigravity"](https://discuss.ai.google.dev/t/google-ai-pro-jio-bundle-all-gemini-4-models-including-hydrogen-neon-marked-unavailable-in-antigravity/202675), plus a thread literally titled ["Gemini 4 Hydrogen and Gemini 4 Argon Models Unavailable with Jio 18-Month Google AI Pro Offer"](https://discuss.ai.google.dev/t/gemini-4-hydrogen-and-gemini-4-argon-models-unavailable-with-jio-18-month-google-ai-pro-offer/200076).
- And if the account is Russian, Antigravity does not even open. Sostav: «Из России по правилам Google пользоваться Antigravity нельзя: в FAQ сервиса перечислены больше 220 стран и территорий, где он доступен, и России среди них нет» ([article](https://www.sostav.ru/blogs/289407/113999)). Meanwhile Google's own forum is a museum of "Your current account is not eligible for Antigravity, because it is not currently available in your location" — written by people with an active AI Pro ([thread](https://discuss.ai.google.dev/t/region-problem-on-account/188118)).

So the pattern is: the good models live inside the app, the app is region-locked, and the subscription you pay for no longer guarantees the models. Three doors, one key, and the key keeps changing shape.

I got tired of waiting for a key. So I built a door.

**Remote Harness MCP** — an open-source MCP server that turns the plain Gemini app (or gemini.google.com) into the control panel for your PC, laptop, server or Android phone. No Antigravity, no VPN gymnastics, no "not eligible", no Ultra upgrade. It works on the free Gemini tier too.

Install it once on the machine you want to control, paste one link into Gemini → Connectors, and Gemini grows hands:

- shell commands, builds, tests, git — on your machine, under your user;
- read / write / edit files, grep and glob search;
- long-running jobs that survive the chat, plus timers and cron;
- screenshots, and system vitals with real numbers (CPU, RAM, disks);
- file and web-page sharing straight from the node;
- and on an Android phone (Termux, no root): battery, signal, torch, camera, notifications.

Things I actually type from my phone:

- "Why is the home server slow? Show the top 5 processes."
- "Pull main in ~/projects/api, run the tests, tell me what broke."
- "Start the backup in the background and ping me when it is done."
- "Screenshot both monitors and describe what is open."
- "Turn on the phone's torch and tell me the battery."

How it works, in three lines:

```text
 📱 Gemini app / 🌐 gemini.google.com
        ⇅  MCP over HTTPS (Tailscale Funnel — no VPS, no port forwarding)
 💻 your PC, laptop, server or phone: commands run here, not in the cloud
```

Install:

```bash
curl -fsSL https://raw.githubusercontent.com/LevRa7/remote-harness-mcp/main/install.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/LevRa7/remote-harness-mcp/main/install.ps1 | iex
```

**"Isn't this a backdoor with extra steps?"** Fair question, so:

- the link is the password: it carries a per-node token, and you can rotate it without reinstalling;
- the node listens on 127.0.0.1 and publishes itself through Tailscale Funnel — no public IP, no open router ports, no root;
- installing or removing software, deleting data, sudo and system paths require explicit confirmation, and `MESH_READ_ONLY=1` makes the machine read-only if you want hard guarantees;
- MIT license: read every line, or point it at a spare machine first.

**The honest catch:** the project is young (v0.7.0 release candidate), it needs a free Tailscale account, and it speaks Gemini only — this is not a multi-model framework. The star count is 6, which is roughly my mother, two bots, and three people who misclicked.

If this saves you even one SSH session from your phone, drop a ⭐ — it is the only thing left in this ecosystem that does not require an upgrade.

Repo: https://github.com/LevRa7/remote-harness-mcp
Bugs, ideas, "please add X" — all welcome below. I will be here all day.

---

## Русская версия

**Заголовок:** Гугл закрыл Antigravity для россиян, а Gemini 4 — для части платных подписок. Я сделал так, чтобы Gemini управлял вашим ПК прямо из приложения

**Текст:**

Хроника последних недель:

- Конец сентября — Ars Technica: ["Google announces Gemini 4 Argon AI model, but you can't use it yet"](https://arstechnica.com/google/2026/09/google-announces-gemini-4-argon-ai-model-but-you-cant-use-it-yet/), через месяц — раскатка ["but only if you pay"](https://arstechnica.com/ai/2026/10/gemini-4-argon-begins-rolling-out-in-gemini-app-and-antigravity-but-only-if-you-pay/).
- 23 ноября — 9to5Google: ["Gemini 4 Carbon is now available in Google Antigravity for AI Pro"](https://9to5google.com/2026/11/23/antigravity-gemini-4-carbon/). Модель есть — но внутри приложения.
- Ноябрь — у кого AI Pro пришёл бандлом от Jio, у того в Antigravity все четыре модели (Argon, Neon, Carbon, Hydrogen) помечены "Unavailable": [тред 1](https://discuss.ai.google.dev/t/google-ai-pro-jio-bundle-all-gemini-4-models-including-hydrogen-neon-marked-unavailable-in-antigravity/202675), [тред 2](https://discuss.ai.google.dev/t/gemini-4-hydrogen-and-gemini-4-argon-models-unavailable-with-jio-18-month-google-ai-pro-offer/200076).
- А для российских аккаунтов Antigravity просто не запускается. Sostav: «Из России по правилам Google пользоваться Antigravity нельзя: в FAQ сервиса перечислены больше 220 стран и территорий, где он доступен, и России среди них нет» ([ссылка](https://www.sostav.ru/blogs/289407/113999)). В официальном форуме Google — очередь из «Your current account is not eligible for Antigravity, because it is not currently available in your location» от людей с оплаченным AI Pro ([тред](https://discuss.ai.google.dev/t/region-problem-on-account/188118)).

Итог: хорошие модели заперли в приложении, приложение — по регионам, а подписка больше не гарантирует модели. Ждать ключ надоело — сделал свою дверь.

**Remote Harness MCP** — open-source MCP-сервер, который превращает обычное приложение Gemini (или gemini.google.com) в пульт управления вашим ПК, ноутбуком, сервером или Android-телефоном. Без Antigravity, без VPN-танцев, без «not eligible», работает и на бесплатном тарифе.

Ставится один раз на машину, одна ссылка вставляется в Gemini → Connectors — и у Gemini появляются руки:

- команды, сборки, тесты, git — на вашей машине, под вашим пользователем;
- чтение/запись/правка файлов, поиск по grep и glob;
- долгие задачи, которые живут после закрытия чата, таймеры и cron;
- скриншоты и живые метрики (CPU, RAM, диски);
- раздача файлов и веб-страниц прямо с узла;
- а на Android в Termux (без root) — батарея, сигнал, фонарик, камера, уведомления.

Что я реально печатаю с телефона:

- «Почему домашний сервер тормозит? Покажи топ-5 процессов.»
- «Сделай pull main в ~/projects/api, прогони тесты, скажи, что упало.»
- «Запусти бэкап в фоне и пингни, когда закончится.»
- «Сделай скриншот обоих мониторов и опиши, что открыто.»
- «Включи фонарик на телефоне и скажи уровень заряда.»

Установка:

```bash
curl -fsSL https://raw.githubusercontent.com/LevRa7/remote-harness-mcp/main/install.sh | bash
```

```powershell
irm https://raw.githubusercontent.com/LevRa7/remote-harness-mcp/main/install.ps1 | iex
```

**«Это же бэкдор с лишними шагами?»** Резонно:

- ссылка — это пароль: в ней токен узла, его можно ротировать без переустановки;
- узел слушает 127.0.0.1 и публикуется через Tailscale Funnel — без белого IP, проброса портов и root;
- установка/удаление софта, удаление данных, sudo и системные пути требуют подтверждения, а `MESH_READ_ONLY=1` делает машину read-only;
- лицензия MIT: читайте каждую строку или сначала ткните в запасную машину.

**Честно про минусы:** проект молодой (v0.7.0 RC), нужен бесплатный аккаунт Tailscale, и он умеет говорить только с Gemini. Звёзд — 6, это примерно мама, два бота и три человека, которые промахнулись мышкой.

Если сэкономит хотя бы одну SSH-сессию с телефона — поставьте ⭐, это единственное в этой экосистеме, что до сих пор не требует апгрейда.

Репозиторий: https://github.com/LevRa7/remote-harness-mcp
Баги, идеи, «добавь вот это» — пишите в комментариях, я тут весь день.

---

## Теги (это не часть поста)

**Reddit — флейр по сабу:**

| Сабреддит | Флейр | Ключевые слова в заголовке |
| :--- | :--- | :--- |
| r/mcp | Show & Tell / Project | MCP server, Gemini, Tailscale, open source |
| r/GeminiAI | Discussion / News | Gemini 4, Antigravity, AI Pro, MCP |
| r/developersIndia | Discussion | Jio, Google AI Pro, Gemini 4, unavailable |
| r/GoogleAntigravity | Tips / Discussion | not eligible, region, workaround, MCP |
| r/selfhosted | Show & Tell | self-hosted, MCP, Tailscale Funnel, no port forwarding |
| r/homelab | Discussion | homelab, remote shell, phone as a node |
| r/Android | Discussion / App | Termux, Gemini, phone as a node |

**Хештеги для кросс-поста в X / LinkedIn / Mastodon:**

`#Gemini #Gemini4 #AIAgents #MCP #ModelContextProtocol #OpenSource #DevTools #Tailscale #Termux #AntiGravity #GoogleAIPro #AIPro #LLM`

**GitHub topics (добавить к репозиторию):**

`gemini`, `mcp`, `model-context-protocol`, `mcp-server`, `ai-agent`, `computer-use`, `remote-execution`, `tailscale`, `tailscale-funnel`, `termux`, `android`, `self-hosted`, `gemini-4`, `google-antigravity`
