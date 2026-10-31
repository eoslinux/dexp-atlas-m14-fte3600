# FocalTech FTE3600 — сканер отпечатков на Linux

Драйвер, прошивка, системная интеграция и графическая утилита для SPI-сенсора
**FocalTech FTE3600**, который встречается в ноутбуках **DEXP Atlas M14**
(и некоторых других моделях на Intel Alder Lake).

Проверено на:

| | |
|---|---|
| Ноутбук | DEXP Atlas M14-I5W304 (i5-1240P, 16 ГБ) |
| Сенсор | FocalTech FTE3600, SPI, ACPI `FTE3600` (`\_SB_.PC00.SPI2.FPNT`) |
| ОС | Debian 13 (trixie), ядро 6.12 |
| Окружение | KDE Plasma 6.3 + GDM 48 |

Что получается в итоге:

- вход по отпечатку на экране входа **GDM**;
- графическая утилита для записи/проверки/удаления отпечатков;
- DKMS-модуль, который переживает обновления ядра;
- всё ставится одним `.deb`.

---

## Содержание

- [Быстрая установка (готовый .deb)](#быстрая-установка-готовый-deb)
- [Что делает пакет](#что-делает-пакет)
- [Графическая утилита](#графическая-утилита)
- [Структура репозитория](#структура-репозитория)
- [Ручная сборка из исходников](#ручная-сборка-из-исходников)
- [Почему GDM, а не SDDM](#почему-gdm-а-не-sddm)
- [Проверка и диагностика](#проверка-и-диагностика)
- [Известные проблемы](#известные-проблемы)
- [Удаление и откат](#удаление-и-откат)
- [Лицензии и благодарности](#лицензии-и-благодарности)

---

## Быстрая установка (готовый .deb)

Готовый пакет — в разделе **Releases** (или соберите сами, см.
[Пересборка .deb](#7-пересборка-deb)).

Требуется Debian 13 (trixie) или совместимый дистрибутив с `fprintd`.

```bash
# 1. Установить пакет (подтянет fprintd, dkms, заголовки ядра, PyQt6)
sudo apt install ./fte3600-fprint_1.1-1_amd64.deb

# 2. Перезагрузиться — пакет добавляет spidev.bufsiz=65536 в GRUB,
#    а этот параметр применяется только при загрузке
sudo reboot
```

После перезагрузки:

1. Войдите паролем и запишите отпечаток (см. ниже) — без записанного
   отпечатка GDM не предложит вход по нему.
2. В меню приложений появится **«Сканер отпечатков»**.
3. После записи отпечатка проверьте вход: выйдите из сессии — на экране GDM
   появится предложение приложить палец.

Первый отпечаток записывается так:

```bash
fte3600-gui          # графически: выбрать палец → «Записать отпечаток»
# или из терминала:
fprintd-enroll -f right-index-finger "$USER"
```

Пакет **не заменяет** системную `libfprint` — он кладёт свою сборку в
`/usr/lib/fte3600` и подключает её только для `fprintd`.

---

## Что делает пакет

| Компонент | Путь |
|---|---|
| libfprint с драйвером `fte3600` | `/usr/lib/fte3600/libfprint-2.so.2` |
| DKMS-модуль ядра `fte3600` | `/usr/src/fte3600-0.2` → `/lib/modules/*/updates/dkms/` |
| Прошивка сенсора `ft9361.bin` | `/usr/lib/firmware/fte3600/` |
| udev: spidev + симлинки `/dev/fte3600-*` | `/etc/udev/rules.d/70-fte3600-acpi-spidev.rules` |
| Параметры spidev | `/etc/modprobe.d/fte3600-acpi-spidev.conf` + GRUB |
| fprintd: `LD_LIBRARY_PATH`, `DeviceAllow`, сброс | `/etc/systemd/system/fprintd.service.d/10-fte3600-acpi-spidev.conf` |
| Сервис сброса сенсора | `fte3600-reset.service` |
| Polkit: fprintd и сброс без пароля | `/etc/polkit-1/rules.d/60-fte3600.rules` |
| GUI и ярлык меню | `/usr/bin/fte3600-gui`, `/usr/share/applications/fte3600-fingerprint.desktop` |

Зависимости: `fprintd`, `dkms`, `linux-headers-amd64`, `python3-pyqt6`,
`libgusb2`, `libgudev-1.0-0`, `libglib2.0-0t64`, `libjson-glib-1.0-0`,
`libusb-1.0-0`, `libudev1`, `systemd`, `udev`. Рекомендуется `gdm3`.

### Технические детали

- `spidev` в ядре Debian **встроен** (`CONFIG_SPI_SPIDEV=y`), поэтому размер
  буфера (`bufsiz`) задаётся только параметром ядра `spidev.bufsiz=65536`.
  Проверить: `cat /sys/module/spidev/parameters/bufsiz` → должно быть `65536`.
- `fprintd` поставляется с `ProtectKernelModules=true`; пакет отключает это
  и разрешает `@module`, иначе невозможно перезагрузить модуль сенсора.
- Перед каждым стартом `fprintd` выполняется `ExecStartPre` со сбросом модуля.
  На этом ноутбуке сенсор после завершения сессии перестаёт выдавать
  прерывание «палец приложен»; симптом — `Fingerprint capture timed out`.
  Перезапуск модуля лечит это за ~3 секунды.
- Прошивка `ft9361.bin` извлечена из Windows-драйвера FocalTech и
  предназначена только для личного использования.

---

## Графическая утилита

Окно **«Сканер отпечатков»** (`fte3600-gui`):

- статус устройства и список записанных пальцев;
- выбор пальца (10 вариантов), «Записать отпечаток», «Проверить», «Удалить все»;
- прогресс 1…8 и подсказки при записи;
- галочка **«Автосброс перед записью/проверкой»** (по умолчанию включена);
- кнопка «Сбросить сенсор»;
- журнал операций.

CLI-режим для диагностики:

```bash
fte3600-gui --list      # устройство и записанные пальцы
fte3600-gui --reset     # сброс сенсора (через fte3600-reset.service)
```

Сброс работает без пароля благодаря polkit-правилу:

```bash
systemctl start fte3600-reset.service
```

---

## Структура репозитория

```text
.
├── build_deb.sh                  # сборка .deb из исходников драйвера
├── fte3600-gui.py                # графическая утилита (PyQt6)
├── fte3600-fingerprint.desktop   # ярлык меню
├── LICENSE                       # лицензия на код утилиты и упаковку
├── README.md                     # этот файл
├── driver/                       # git submodule: SamSeven777/libfprint-fte3600
└── dist/                         # готовый .deb
```

Исходники драйвера подключены сабмодулем
[SamSeven777/libfprint-fte3600](https://github.com/SamSeven777/libfprint-fte3600).
Клонировать репозиторий нужно так:

```bash
git clone --recurse-submodules <url>
# если уже склонировали без сабмодуля:
git submodule update --init --recursive
```

---

## Ручная сборка из исходников

Нужны исходники драйвера
[SamSeven777/libfprint-fte3600](https://github.com/SamSeven777/libfprint-fte3600)
и собранный `build/libfprint`.

### 1. Зависимости

```bash
sudo apt install build-essential pkg-config meson ninja-build dkms \
  linux-headers-amd64 libglib2.0-dev libgusb-dev libgudev-1.0-dev \
  libudev-dev systemd-dev python3-pyqt6 fprintd
```

### 2. Параметр ядра для spidev

Так как `spidev` собран в ядро, добавьте в `/etc/default/grub`:

```text
GRUB_CMDLINE_LINUX_DEFAULT="quiet spidev.bufsiz=65536"
```

```bash
sudo update-grub
sudo reboot
```

После перезагрузки проверьте:

```bash
cat /sys/module/spidev/parameters/bufsiz   # 65536
```

### 3. Драйвер и модуль ядра

Исходники драйвера лежат в сабмодуле `driver/`:

```bash
git submodule update --init --recursive
cd driver

# DKMS-модуль-прослойка (SPI + GPIO сброс + IRQ)
sudo ./scripts/setup-fte3600.sh install-kernel

# Сборка libfprint с драйвером fte3600
meson setup build --prefix=/usr --buildtype=release -Ddrivers=fte3600 \
  -Dgtk-examples=false -Ddoc=false -Dintrospection=false -Dinstalled-tests=false
meson compile -C build
sudo meson install -C build
sudo ldconfig

# Интеграция с fprintd
sudo ./scripts/setup-fte3600.sh install-systemd
```

### 4. Прошивка

```bash
sudo ./scripts/install-firmware.sh --chip ft9361 --download \
  --destdir /usr/lib/firmware
```

### 5. Проверка устройства

```bash
fprintd-list "$USER"
# ожидается: FocalTech FTE3600 (host BRISK)
```

### 6. GDM и polkit

```bash
sudo apt install gdm3
sudo systemctl disable sddm 2>/dev/null || true
sudo ln -sf /usr/lib/systemd/system/gdm.service \
  /etc/systemd/system/display-manager.service
echo /usr/sbin/gdm3 | sudo tee /etc/X11/default-display-manager
```

Polkit-правило (управление сканером и сброс без пароля):

```js
// /etc/polkit-1/rules.d/60-fte3600.rules
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("net.reactivated.fprint.device.") === 0) {
        return polkit.Result.YES;
    }
    if (action.id === "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") === "fte3600-reset.service") {
        return polkit.Result.YES;
    }
});
```

Перезагрузитесь и запишите отпечаток:

```bash
fprintd-enroll -f right-index-finger "$USER"
```

### 7. Пересборка .deb

Если драйвер уже собран (есть `driver/build/libfprint/libfprint-2.so.2.0.0`):

```bash
sudo ./build_deb.sh "$PWD/driver"
# результат: ~/fte3600-deb/fte3600-fprint_1.1-1_amd64.deb
```

---

## Почему GDM, а не SDDM

- **SDDM отпечатки не умеет** — ни кнопки, ни автозапуска. Работает только
  через PAM: нужно нажать Enter и успеть приложить палец, а стандартная тема
  даже не показывает подсказку. Плюс ввод пароля тормозит на таймаут PAM.
- **GDM 48** содержит отдельный PAM-сервис `gdm-fingerprint` и штатный UI:
  на экране входа сам предлагает отпечаток, пароль работает параллельно.
- SDDM можно оставить установленным — он просто отключён. Откат:
  см. [Удаление и откат](#удаление-и-откат).

---

## Проверка и диагностика

```bash
# устройство и записанные пальцы
fprintd-list "$USER"

# статус демона и его окружение
systemctl status fprintd
systemctl show fprintd -p Environment

# журнал драйвера и совпадений
journalctl -u fprintd -b

# проверка конкретного пальца
fprintd-verify -f right-index-finger "$USER"

# состояние модуля и DKMS
lsmod | grep fte3600
dkms status
```

Ожидаемые строки в журнале при успешном распознавании:

```text
FTE3600 match sample N: status=0 accepted=1 inliers=...
verify_cb: result verify-match
```

---

## Известные проблемы

| Симптом | Причина / решение |
|---|---|
| `Fingerprint capture timed out` | Сенсор «залип» после сессии. `systemctl start fte3600-reset.service` или кнопка «Сбросить сенсор» |
| `No devices available` | Не применён `spidev.bufsiz=65536` (перезагрузиться) или не загружен модуль: `sudo modprobe fte3600` |
| Устройство есть, но `verify-no-match` | Перезаписать отпечаток аккуратно, палец по центру: `fprintd-delete "$USER" && fprintd-enroll -f right-index-finger "$USER"` |
| `Not Authorized: net.reactivated.fprint.device.verify` | Нет polkit-правила (см. выше) |
| Отпечаток не разблокирует KWallet | Ограничение fprintd: для сохранённых паролей нужен пароль |
| `modprobe: Module fte3600 not found` | Модуль не собран под текущее ядро: `sudo dkms install -m fte3600 -v 0.2 --force` |
| В GDM нет отпечатка | Не установлен `gdm3`, либо отпечаток не записан, либо активен SDDM |

---

## Удаление и откат

```bash
# удалить пакет
sudo apt remove fte3600-fprint

# вернуть SDDM (если нужно)
sudo rm -f /etc/systemd/system/display-manager.service
sudo ln -s /usr/lib/systemd/system/sddm.service \
  /etc/systemd/system/display-manager.service
echo /usr/sbin/sddm | sudo tee /etc/X11/default-display-manager
sudo reboot
```

Параметр `spidev.bufsiz=65536` в GRUB и файл прошивки при удалении не
удаляются автоматически (чтобы не ломать загрузку). При необходимости
уберите их вручную из `/etc/default/grub` и `/usr/lib/firmware/fte3600`.

---

## Лицензии и благодарности

- Драйвер libfprint и glue-модуль — [SamSeven777/libfprint-fte3600](https://github.com/SamSeven777/libfprint-fte3600),
  LGPL-2.1-or-later (host-код), GPL-2.0-only (kernel glue).
- libfprint — LGPL-2.1-or-later.
- Графическая утилита и упаковка — этот репозиторий.
- Прошивка `ft9361.bin` — собственность FocalTech Systems, извлечена из
  Windows-драйвера, распространяется для личного использования.
