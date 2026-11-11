# Project Plan: Remote-Harness Live USB Edition (`scenario="live_usb"`)

## 1. Архитектурное видение
Интеграция режима **Live USB Agent** напрямую в ядро `remote-harness-mcp` (`D:\MyProjects\Antigravity-mash`).
Базовые принципы кодовой базы сохраняются:
- **Zero third-party dependencies** в рантайме узла (`core/` использует только стандартную библиотеку Python 3.8+).
- **Никаких необработанных исключений**: любой сбой возвращается как `{"error": "..."}` или `{"available": False, "reason": "..."}`.
- **Детерминированный выбор сценария**: расширение `core/device.py::scenario()` поддержкой `live_usb` (определяется по `/run/live/medium`, `MESH_SCENARIO=live_usb` или параметру ядра `boot=live`).
- **Разделение прав (`TOOL_ANNOTATIONS`)**: диагностика (`rescue_info`) работает в `_ANNOTATIONS_READ_ONLY`, а любые модификации разделов, загрузчика и chroot-восстановление (`rescue_action`) проходят через `_ANNOTATIONS_DESTRUCTIVE` (с подтверждением оператора и поддержкой `dry_run=True`).

---

## 2. Дорожная карта по этапам

### Этап 1: Ядро диагностики и восстановления (`core/rescue.py` & `core/mcp_tools.py`)
- [x] **Проектирование контрактов и JSON-схем (`core/rescue.py`)**:
  - Секции `rescue_info`: `disks`, `smart`, `partitions`, `raid_lvm`, `installed_os`, `boot_efi`, `win_artifacts`, `dmesg_io`.
  - Действия `rescue_action`: `mount_safe`, `umount`, `fsck_check`, `ntfs_fix`, `grub_repair`, `win_sam_reset`, `ddrescue_start`, `chroot_exec`.
- [ ] **Интеграция в `core/mcp_tools.py`**:
  - Подключение `rescue_info` (`_ANNOTATIONS_READ_ONLY`) и `rescue_action` (`_ANNOTATIONS_DESTRUCTIVE`).
  - Связка `ddrescue_start` и долгих проверок `fsck` со встроенным менеджером фоновых задач (`run_job` / `job_output`).
- [ ] **Покрытие unit-тестами (`tests/test_rescue.py`)**:
  - Фикстуры вывода `lsblk -J`, `smartctl -a -j`, `sfdisk -J`, `efibootmgr` по аналогии с `capture-termux-fixtures.sh`.

### Этап 2: Сетевой стек и First-Boot TUI (`ops/live-usb/firstboot_tui.py`)
- [ ] **Zero-Touch Network**:
  - Автоподъем Ethernet, Wi-Fi (через `nmcli`) и USB-модемов / Android RNDIS (`usb0`).
  - Защита активного аплинка (`active_uplink_lock`): запрет сброса сетевого интерфейса, через который установлен туннель к шлюзу.
- [ ] **Консольный дашборд на `tty1`**:
  - Отрисовка статуса туннеля (`core/agent.py`), локального IP, состояния дисков и ASCII QR-кода со ссылкой на Web UI координатора.

### Этап 3: Конвейер сборки образа Debian Live (`ops/live-usb/`)
- [ ] **Скрипт инициализации `live-build` (`ops/live-usb/build-iso.sh`)**:
  - База: Debian Trixie/Bookworm (`main contrib non-free non-free-firmware`).
  - Включение всех пакетов прошивок сетевых карт (`firmware-iwlwifi`, `firmware-realtek`, `firmware-amd-graphics`, `firmware-misc-nonfree`).
  - Установка утилит диагностики (`smartmontools`, `nvme-cli`, `gddrescue`, `testdisk`, `chntpw`, `efibootmgr`, `lvm2`, `mdadm`, `cryptsetup`, `btrfs-progs`, `ntfs-3g`).
- [ ] **Systemd-интеграция в SquashFS**:
  - `remote-harness-agent.service` — запуск туннельного агента сразу после `network-online.target`.
  - Отключение автомонтирования дисков на уровне `udev`, чтобы не повредить журналируемые ФС на умирающих носителях.
  - Ограничение `Storage=volatile` и `SystemMaxUse=50M` в `journald.conf` для защиты `tmpfs` от переполнения.
