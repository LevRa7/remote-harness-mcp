# Sandbox: модель угроз и защита

**Область:** профиль `rhm` → `i` (strict) / `I` (network) — контейнер для
разработки и запуска сомнительного кода.

## Что монтируется и что доступно

| Ресурс | В контейнере | Защита |
| :--- | :--- | :--- |
| Каталог проекта | `/workspace` (rw) | только выбранный проект; системные корни и весь home запрещены |
| Остальная ФС хоста | нет | нет bind-монтирований, кроме проекта |
| Docker socket | нет | `docker.sock` не монтируется |
| Capabilities | нет | `--cap-drop ALL` |
| Привилегии | нет | `--security-opt no-new-privileges` |
| PID / память / CPU | лимиты | `--pids-limit 512`, `--memory 2g`, `--cpus 2`, `--ulimit` |
| `/tmp` | tmpfs | `noexec,nosuid`, 512 МБ |
| Сеть | **none** по умолчанию | strict-режим полностью отрезан |
| Сеть с разрешения | bridge | только по `I` + явное подтверждение `y` |
| Docker runtime | runsc/kata, если есть | fallback docker-default |
| SSH к хосту | только в network-режиме | нет ключей/agent forwarding; включение требует подтверждения |

## Векторы побега и меры

1. **Docker socket / privileged** — не монтируется, `--privileged` не используется.
2. **Лишние capabilities** — `--cap-drop ALL`; `no-new-privileges` не даёт поднять их обратно.
3. **Host mount** — только проект; `workspace_refusal()` запрещает `/`, `/etc`,
   `/usr`, `/var`, `/root`, весь `%USERPROFILE%`, корень диска Windows и
   `C:\Windows`/`Program Files`.
4. **Symlink `.rhm-sandbox` наружу** — реальный путь проверяется и должен лежать
   внутри проекта.
5. **Сеть / metadata / LAN / SSH** — strict-режим `--network none`; сеть только
   через `I` с подтверждением. Так «по SSH можно, но с подтверждения»: без
   network-режима SSH из контейнера невозможен, с ним — только после явного `y`.
6. **Ресурсный DoS** — лимиты PID, RAM, CPU, file descriptors.
7. **Kernel exploit** — default seccomp/AppArmor; при наличии автоматически
   выбирается gVisor (`runsc`) или Kata. Это не абсолютная гарантия против
   kernel-0day; для недоверенного кода используйте rootless Docker + gVisor.
8. **Утечка env/токенов** — `docker run` не получает `MESH_*`; `docker exec`
   выполняется с argv, без host shell и без `-e`.
9. **Запись в проект** — ожидаемо: контейнеру нужен код; `.rhm-sandbox/`
   исключён из git, состояние управления лежит вне проекта.

## Остаточные риски

- root внутри контейнера (нужен для `apt`); при kernel-эксплойте это опасно —
  используйте gVisor/rootless.
- Docker daemon обычно rootful; компрометация daemon = компрометация хоста.
- `system_info`/`system_vitals` и device-ветка остаются на хосте: это телеметрия
  узла, а не команды; полная изоляция — отдельный профиль.
- Network-режим даёт доступ к LAN и хостовым сервисам, включая SSH, если он
  открыт. Включайте только на время установки пакетов и выключайте обратно.
- Per-command подтверждение SSH не реализовано: подтверждается включение
  network-режима. Следующий шаг — отдельный approval на `ssh`.

## Проверки

- `tests/test_sandbox.py`: конфайнмент путей, `/workspace` mapping, запрет
  системных корней, обязательный confirm для network, флаги `--network none`,
  `--cap-drop ALL`, отсутствие `docker.sock`/`--privileged`.
- `tests/test_web_settings.py`: token/CSRF, режимы, страница настроек.
