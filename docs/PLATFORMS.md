# Платформенный слой: Windows · Linux · Android/Termux

Узел отдаёт одни и те же MCP-инструменты на сервере, на Windows-машине и на
телефоне, но за ними стоят разные ОС: разная оболочка, разные источники
телеметрии, разные правила для процессов и разное отношение к экрану. Раньше эти
различия были ветками `if os.name == "nt"` внутри `core/mcp_tools.py`,
`core/vitals.py`, `core/device.py` и `core/termux.py` — правка в одном месте
ломала другое, и ни один файл не отвечал на вопрос «что этот узел делает на
телефоне».

Теперь каждая платформа — отдельный каталог с полной реализацией.

```
core/platforms/
├── __init__.py          детект сценария + реестр backend()
├── base.py              контракт PlatformBackend
├── common.py            переносимые помощники и общие форматы блоков
├── shell.py             выбор оболочки (чистая функция)
├── encoding.py          декодирование вывода дочерних процессов (чистая функция)
├── png.py               PNG-энкодер на zlib (для скриншотов, без Pillow)
├── windows/             Win32 через ctypes: без PowerShell, WMI, git и WSL
│   ├── winapi.py        объявления структур и функций ядра
│   ├── telemetry.py     RAM/CPU/диски/процессы/uptime из ядра
│   ├── process.py       liveness, дерево процессов, TerminateProcess
│   ├── screen.py        скриншот через GDI + PNG
│   └── clipboard.py     буфер обмена через user32
├── linux/               /proc и /sys: без df, free, ps, uptime
│   ├── procfs.py        парсеры /proc (их же использует Android)
│   ├── telemetry.py     витальные метрики и блоки system_info
│   └── process.py       liveness и kill по /proc
└── android/             /proc и /sys, Termux:API только там, где иначе нельзя
    ├── sysfs.py         батарея, термические зоны, сеть, хранилище
    ├── telemetry.py     витальные метрики + батарея + температуры
    └── termux.py        мост termux-* (бывший core/termux.py)
```

`core/termux.py` остался как совместимый алиас: он подставляет **тот же объект
модуля** в `sys.modules`, поэтому `import core.termux` и
`core.platforms.android.termux` — это один модуль, и `monkeypatch` в тестах
доходит до кода, который реально выполняется.

### Почему `core/device.py` остался на месте

`core/device.py` — уже платформенная матрица: `_SECTION_IMPL[секция][сценарий]`
даёт реализацию на каждый из четырёх сценариев, каждая секция живёт на чистом
stdlib и деградирует в `available: false` с причиной, а не падает. Перенос её
строк по каталогам дал бы больше файлов, но не убрал бы ни одной зависимости —
и сломал бы контракт `monkeypatch` (`device._run`, `device._which`,
`device._termux_adapter`), на котором держится 117 сценариев в тестах. Что
действительно было вторым решением — детект сценария — оттуда убран:
`device.scenario()` теперь передаёт свои сигналы в `platforms.detect()`.

## Контракт

Один класс на платформу, `PlatformBackend` (`core/platforms/base.py`):

| Метод | Что отвечает |
| :--- | :--- |
| `shell()` | `(имя, argv-префикс)` оболочки для `bash_exec` / `run_job` |
| `popen_flags()`, `child_env()` | флаги создания процессов и окружение потомка |
| `output_encodings()` | в каком порядке пробовать кодировки при декодировании вывода |
| `uptime_seconds()` | секунды с загрузки или `None` |
| `vitals()` | CPU/RAM/диск в форме `system_vitals` |
| `info_blocks()` | четыре текстовых блока `system_info`: `disks`, `memory`, `load`, `top_processes` |
| `wallpaper()` | текущие обои рабочего стола или `""` |
| `pid_alive()`, `pid_start_time()`, `kill_tree()` | жизнь и завершение процессов |
| `screenshot_png()` | PNG экрана или `None` (нет экрана — это ответ, а не ошибка) |
| `clipboard_get()`, `clipboard_set()` | буфер обмена, если платформа его даёт |
| `capabilities()`, `report()` | что реально доступно на этой машине |

Правила: **только стандартная библиотека**, **ничего не бросает**, отсутствующая
возможность возвращает причину. Ключи блоков одинаковы на всех платформах —
модель, выучившая таблицу на Linux, читает Windows и Android без переобучения.

## Что и чем заменено

| Значение | Было | Стало |
| :--- | :--- | :--- |
| RAM, диски, uptime, CPU (Windows) | `Get-CimInstance`, `LastBootUpTime`, `Win32_Processor` (PowerShell + WMI, 1–3 c) | `GlobalMemoryStatusEx`, `GetDiskFreeSpaceExW`, `GetTickCount64`, `GetSystemTimes` (микросекунды) |
| Список процессов | `Get-Process` (PowerShell) | `Toolhelp32` + `GetProcessMemoryInfo` |
| RAM, диски, load (Linux) | `free`, `df`, `uptime`, `ps` | `/proc/meminfo`, `statvfs`, `/proc/uptime`, `/proc/loadavg` |
| Процессы (Linux/Android) | `ps`, `kill` | `/proc/<pid>/stat`, `os.kill` |
| Батарея (Android) | `termux-battery-status` | `/sys/class/power_supply` → если SELinux не даёт, `termux-battery-status` |
| Термометры (Android) | `termux-*` | `/sys/class/thermal/thermal_zone*` |
| Скриншот (Windows) | не было | GDI `BitBlt` → PNG на `zlib` |

Windows-путь не запускает ни одного процесса: всё — вызовы ядра. Linux-путь не
запускает ни одного: всё — чтение файлов. Android запускает `termux-*` только
для того, что песочница Android действительно запрещает приложению (камера, SMS,
геолокация) и для буфера обмена.

## Проверено вживую

Windows 11 IoT Enterprise LTSC 26100, Python 3.13, **без** установленного
PowerShell 7 (используется то, что даёт ОС):

```
disks          C:\ 120.0G / 114.7G / 5.3G    D:\ 341.2G / 283.9G / 57.3G
memory         Mem: 15.4G total, 12.5G used, 2.8G free
load           CPU 1% | cores 16 | booted 2026-11-11 04:02 | up 6.9 h
top_processes  llama-server.exe 2658.5 MB, chrome.exe 915.5 MB, ...
screenshot     2520x1680 PNG (per-monitor DPI aware), ~260 KB
clipboard      запись/чтение round-trip
kill_tree      родитель + потомок завершены
```

Android 16 (OPPO PHY110, Termux, Python 3.14.6), живой прогон
`platform_check.py` на самом телефоне — **24/24**:

```
detect()                       termux
shell                          bash (/data/data/com.termux/files/usr/bin/bash)
vitals: RAM / диск             15204 MB / 455.3 GB
battery                        100% via termux-battery-status, charging=True
thermal                        4 зоны, максимум 47.7 C
info_blocks                    disks, memory, load, top_processes — непустые
pid_alive / kill_tree          свои процессы видны, дерево завершается
clipboard                      запись ok, чтение "" (Android скрывает от фона)
```

## Ограничения платформ (честно)

* **Android 16 / SELinux** не даёт приложению читать `/proc/uptime`,
  `/proc/stat`, `/proc/loadavg`, `/sys/class/power_supply`,
  `/sys/class/net/*`. Читаются `/proc/meminfo` и `/proc/<pid>/stat`.
  Следствие: на таком телефоне `uptime` неизвестен (`None`, блок `load`
  показывает только `cores`), системный CPU% недоступен, батарея приходит из
  `termux-battery-status`, а не из sysfs. Это проверено на устройстве, а не
  предположено.
* **Буфер обмена Android** доступен на чтение только приложению в фокусе
  (Android 10+). Узел в фоне пишет в буфер, но читает пустую строку — это ответ
  ОС, а не сбой.
* **Скриншот на Android** невозможен: `screencap` требует группу `graphics`,
  у Termux:API команды экрана нет. Инструмент возвращает `available: false` с
  причиной.
* **Скриншот на Linux** — `None`, если у процесса нет дисплея (сервер,
  контейнер). Для X11-сессии нужен отдельный backend, его пока нет.
* **macOS** реализован частично (`DarwinBackend`): оболочка, load, диски,
  память через `sysctl`/`vm_stat`; дерево процессов и скриншот — нет.
* **Термозоны Android** бывают трёх видов: миллиградусы (`38300` = 38.3 °C),
  градусы и счётчики уровней (`750`). Значения вне диапазона −50…150 °C
  отбрасываются, поэтому «750 °C» больше не появляется.

## Тесты

```bash
# весь слой платформ, на любой ОС (fixtures подменяют /proc и /sys)
python -m pytest -q tests/test_platforms.py tests/test_screen_capture.py

# Windows-путь проверяется живьём, если тесты идут на Windows
python -m pytest -q tests/test_platforms.py -k windows
```

Linux/Android-парсеры тестируются на поддельном дереве `/proc` (класс `ProcFS`
принимает root), поэтому их можно проверять на Windows-машине. Живая проверка
телефона — `tools/platform_check.py` (24 проверки: детект, оболочка, витальные
метрики, батарея, термозоны, блоки `system_info`, PID и дерево процессов, буфер
обмена). Доставить его в Termux можно одноразовым HTTP-сервером и `adb reverse`:

```bash
python -m http.server 8099 --bind 127.0.0.1 --directory tools &
adb reverse tcp:8099 tcp:8099
adb shell "run-as com.termux sh -c 'cd /data/data/com.termux/files/home && \
    curl -s http://127.0.0.1:8099/platform_check.py -o platform_check.py && \
    curl -s http://127.0.0.1:8099/platform_check.sh -o platform_check.sh'"
adb shell run-as com.termux sh /data/data/com.termux/files/home/platform_check.sh
```

Скрипт отказывается работать не на Termux (`exit 2`), поэтому его безопасно
запускать где угодно.

## Как добавить платформу

1. Создать `core/platforms/<имя>/__init__.py` с классом-наследником
   `PlatformBackend`; реализовать `shell()` и `vitals()`, остальное наследуется
   и отвечает «недоступно».
2. Добавить имя в `SCENARIOS` и ветку в `_build()` в `core/platforms/__init__.py`.
3. Добавить `tests/test_platforms.py`-кейсы: детект, форма `vitals`, формат
   блоков, поведение при отсутствии возможности.
4. Прогнать `pytest -q` и живой прогон на целевой машине.
