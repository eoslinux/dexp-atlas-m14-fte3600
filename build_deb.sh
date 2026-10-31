#!/bin/sh
# Сборка Debian-пакета fte3600-fprint.
# Запуск от root на целевой машине:  sudo ./build_deb.sh
set -eu

VERSION="1.1"
PKG="fte3600-fprint"
ARCH="amd64"
ROOT="/tmp/build/${PKG}_${VERSION}-1_${ARCH}"
USER_HOME=$(getent passwd "${SUDO_USER:-arseniy}" | cut -d: -f6)
OUT="$USER_HOME/fte3600-deb"
SRC="$USER_HOME/fte3600"
PKGSRC="/tmp/pkg-src"

[ "$(id -u)" = "0" ] || { echo "run as root" >&2; exit 1; }
[ -d "$SRC/build" ] || { echo "no $SRC/build (libfprint build)" >&2; exit 1; }

rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/usr/lib/fte3600" \
         "$ROOT/usr/lib/firmware/fte3600" \
         "$ROOT/usr/libexec" \
         "$ROOT/usr/lib/systemd/system" \
         "$ROOT/etc/systemd/system/fprintd.service.d" \
         "$ROOT/etc/udev/rules.d" \
         "$ROOT/etc/modprobe.d" \
         "$ROOT/etc/polkit-1/rules.d" \
         "$ROOT/usr/src/fte3600-0.2" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/doc/$PKG" \
         "$OUT"

# --- libfprint с драйвером fte3600 (приватная копия) ---
install -m 0644 "$SRC/build/libfprint/libfprint-2.so.2.0.0" "$ROOT/usr/lib/fte3600/"
ln -s libfprint-2.so.2.0.0 "$ROOT/usr/lib/fte3600/libfprint-2.so.2"

# --- прошивка сенсора ---
install -m 0644 /usr/lib/firmware/fte3600/ft9361.bin "$ROOT/usr/lib/firmware/fte3600/"

# --- исходники DKMS-модуля ---
for f in Makefile dkms.conf fte3600.c fte3600-policy-test.c; do
  install -m 0644 "$SRC/kernel/fte3600/$f" "$ROOT/usr/src/fte3600-0.2/"
done
for f in "$SRC"/kernel/fte3600/*.h; do
  install -m 0644 "$f" "$ROOT/usr/src/fte3600-0.2/"
done

# --- udev / modprobe ---
install -m 0644 "$SRC/config/udev/70-fte3600-acpi-spidev.rules" "$ROOT/etc/udev/rules.d/"
install -m 0644 "$SRC/config/modprobe.d/fte3600-acpi-spidev.conf" "$ROOT/etc/modprobe.d/"

# --- helper сопряжения устройств ---
install -m 0755 "$SRC/scripts/fte3600-pair.py" "$ROOT/usr/libexec/fte3600-pair"

# --- GUI ---
install -m 0755 "$PKGSRC/fte3600-gui.py" "$ROOT/usr/bin/fte3600-gui"
sed 's|/usr/local/bin/fte3600-gui|/usr/bin/fte3600-gui|' \
  "$PKGSRC/fte3600-fingerprint.desktop" \
  > "$ROOT/usr/share/applications/fte3600-fingerprint.desktop"
chmod 0644 "$ROOT/usr/share/applications/fte3600-fingerprint.desktop"

# --- сброс сенсора ---
cat > "$ROOT/usr/libexec/fte3600-reset-module" <<'EOS'
#!/bin/sh
# Перезапуск модуля fte3600 (без остановки fprintd).
modprobe -r fte3600 2>/dev/null || true
sleep 1
modprobe fte3600 || exit 1
udevadm settle --timeout=5 2>/dev/null || true
exit 0
EOS
chmod 0755 "$ROOT/usr/libexec/fte3600-reset-module"

cat > "$ROOT/usr/libexec/fte3600-reset" <<'EOS'
#!/bin/sh
# Полный сброс: остановить fprintd, перезагрузить модуль.
# Следующий вызов fprintd поднимет его заново (D-Bus activation).
systemctl stop fprintd.service 2>/dev/null || true
exec /usr/libexec/fte3600-reset-module
EOS
chmod 0755 "$ROOT/usr/libexec/fte3600-reset"

cat > "$ROOT/usr/lib/systemd/system/fte3600-reset.service" <<'EOS'
[Unit]
Description=Reset FocalTech FTE3600 fingerprint sensor
Documentation=file:///usr/share/doc/fte3600-fprint/README.md

[Service]
Type=oneshot
ExecStart=/usr/libexec/fte3600-reset
EOS
chmod 0644 "$ROOT/usr/lib/systemd/system/fte3600-reset.service"

# --- fprintd: приватная библиотека, доступ к устройствам, сброс перед стартом ---
cat > "$ROOT/etc/systemd/system/fprintd.service.d/10-fte3600-acpi-spidev.conf" <<'EOS'
[Service]
# Приватная сборка libfprint с драйвером fte3600
Environment=LD_LIBRARY_PATH=/usr/lib/fte3600
# Доступ к spidev/gpio/uio, созданным udev-правилом
DeviceAllow=/dev/fte3600-spi-FTE3600:00 rw
DeviceAllow=/dev/fte3600-gpio-FTE3600:00 rw
DeviceAllow=/dev/fte3600-irq-FTE3600:00 r
# fprintd штатно запрещает загрузку модулей, а сброс без этого невозможен
ProtectKernelModules=false
SystemCallFilter=@module
ExecStartPre=/usr/libexec/fte3600-reset-module
EOS
chmod 0644 "$ROOT/etc/systemd/system/fprintd.service.d/10-fte3600-acpi-spidev.conf"

# --- polkit: fprintd и сброс без пароля ---
cat > "$ROOT/etc/polkit-1/rules.d/60-fte3600.rules" <<'EOS'
// Управление сканером FTE3600 и его сброс без запроса пароля.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("net.reactivated.fprint.device.") === 0) {
        return polkit.Result.YES;
    }
    if (action.id === "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") === "fte3600-reset.service") {
        return polkit.Result.YES;
    }
});
EOS
chmod 0644 "$ROOT/etc/polkit-1/rules.d/60-fte3600.rules"

# --- документация ---
install -m 0644 "$PKGSRC/README.md" "$ROOT/usr/share/doc/$PKG/README.md"
cat > "$ROOT/usr/share/doc/$PKG/copyright" <<'EOS'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/

Files: *
Copyright: 2026 FTE3600 Linux contributors
License: LGPL-2.1-or-later

Files: usr/lib/firmware/fte3600/ft9361.bin
Copyright: FocalTech Systems
License: proprietary
 Firmware payload extracted from the vendor Windows driver. Redistribution
 is not permitted by the vendor; included here for personal use only.
EOS

# --- control ---
cat > "$ROOT/DEBIAN/control" <<EOS
Package: $PKG
Version: $VERSION-1
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: arseniy <arseniy@localhost>
Depends: fprintd, dkms, linux-headers-amd64, python3-pyqt6, libgusb2, libgudev-1.0-0, libglib2.0-0t64, libjson-glib-1.0-0, libusb-1.0-0, libudev1, systemd, udev
Recommends: gdm3
Description: FocalTech FTE3600 fingerprint reader support
 Driver (libfprint + DKMS module), firmware, udev/systemd integration
 and a small PyQt6 utility for enrolling and verifying fingerprints
 on laptops with the FocalTech FTE3600 SPI sensor (DEXP Atlas M14).
EOS

cat > "$ROOT/DEBIAN/postinst" <<'EOS'
#!/bin/sh
set -e

# 1. spidev built into the Debian kernel: buffer size must be set on cmdline
if [ -f /etc/default/grub ] && ! grep -q "spidev.bufsiz=65536" /etc/default/grub; then
    cp -n /etc/default/grub /etc/default/grub.bak-fte3600 2>/dev/null || true
    sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 spidev.bufsiz=65536"/' /etc/default/grub
    update-grub >/dev/null 2>&1 || true
fi

# 2. DKMS module
if [ -d "/lib/modules/$(uname -r)/build" ]; then
    dkms add -m fte3600 -v 0.2 >/dev/null 2>&1 || true
    dkms build -m fte3600 -v 0.2 >/dev/null 2>&1 || true
    dkms install -m fte3600 -v 0.2 --force >/dev/null 2>&1 || true
fi
depmod -a >/dev/null 2>&1 || true

# 3. udev + systemd
udevadm control --reload >/dev/null 2>&1 || true
udevadm trigger --action=change --subsystem-match=spi >/dev/null 2>&1 || true
udevadm settle --timeout=5 >/dev/null 2>&1 || true
modprobe fte3600 >/dev/null 2>&1 || true
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl try-restart fprintd.service >/dev/null 2>&1 || true

exit 0
EOS
chmod 0755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/prerm" <<'EOS'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        systemctl stop fprintd.service >/dev/null 2>&1 || true
        dkms remove -m fte3600 -v 0.2 --all >/dev/null 2>&1 || true
        ;;
esac
exit 0
EOS
chmod 0755 "$ROOT/DEBIAN/prerm"

cat > "$ROOT/DEBIAN/postrm" <<'EOS'
#!/bin/sh
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
udevadm control --reload >/dev/null 2>&1 || true
depmod -a >/dev/null 2>&1 || true
exit 0
EOS
chmod 0755 "$ROOT/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "$ROOT" "$OUT/${PKG}_${VERSION}-1_${ARCH}.deb"
echo "BUILT: $OUT/${PKG}_${VERSION}-1_${ARCH}.deb"
