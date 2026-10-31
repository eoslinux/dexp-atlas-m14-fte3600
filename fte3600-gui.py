#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Маленькая графическая утилита для сканера отпечатков FocalTech FTE3600.

Работает поверх fprintd (fprintd-enroll / fprintd-verify / fprintd-list /
fprintd-delete) и умеет перезапускать модуль-прослойку fte3600 — без этого
сенсор после одной сессии перестаёт видеть палец (известная особенность
драйвера на этом ноутбуке).

Запуск:  fte3600-gui
Диагностика без GUI:  fte3600-gui --list | --reset
"""

import os
import re
import shutil
import subprocess
import sys

from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

RESET_SERVICE = "fte3600-reset.service"
ENROLL_SAMPLES = 8

FINGERS = [
    ("right-index-finger", "Правый указательный"),
    ("left-index-finger", "Левый указательный"),
    ("right-thumb", "Правый большой"),
    ("left-thumb", "Левый большой"),
    ("right-middle-finger", "Правый средний"),
    ("left-middle-finger", "Левый средний"),
    ("right-ring-finger", "Правый безымянный"),
    ("left-ring-finger", "Левый безымянный"),
    ("right-little-finger", "Правый мизинец"),
    ("left-little-finger", "Левый мизинец"),
]

FINGER_NAMES = dict(FINGERS)


def run_env():
    env = QProcessEnvironment.systemEnvironment()
    env.insert("LANG", "C.UTF-8")
    env.insert("LC_ALL", "C.UTF-8")
    return env


def run_sync(args, timeout=20):
    """Синхронный вызов вспомогательной команды."""
    try:
        cp = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        return cp.returncode, cp.stdout.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def read_state():
    """Возвращает (device_name, [fingers]) или (None, []) при ошибке/нет прав."""
    rc, out = run_sync(["fprintd-list", os.environ.get("USER", "")])
    device = None
    fingers = []
    m = re.search(r"on (.+?) \(press\):", out)
    if m:
        device = m.group(1).strip()
    for line in out.splitlines():
        fm = re.match(r"\s*-\s*#\d+:\s*(\S+)", line)
        if fm:
            fingers.append(fm.group(1))
    if rc != 0 and device is None:
        return None, []
    return device, fingers


class FingerprintWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.proc = None
        self.phase = None
        self.enroll_count = 0
        self.auto_reset = True
        self._build_ui()
        self.refresh_state()

    # ---------- UI ----------
    def _build_ui(self):
        self.setWindowTitle("Сканер отпечатков — FTE3600")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)

        title = QLabel("Сканер отпечатков пальцев")
        f = title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        subtitle = QLabel("FocalTech FTE3600 · DEXP Atlas M14")
        subtitle.setStyleSheet("color: #7a7a7a;")
        root.addWidget(subtitle)

        # Состояние
        box_state = QGroupBox("Состояние")
        state_layout = QVBoxLayout(box_state)
        self.device_label = QLabel("Устройство: проверяю…")
        self.enrolled_label = QLabel("Записанные пальцы: —")
        self.enrolled_label.setWordWrap(True)
        state_layout.addWidget(self.device_label)
        state_layout.addWidget(self.enrolled_label)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.clicked.connect(self.refresh_state)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        state_layout.addLayout(row)
        root.addWidget(box_state)

        # Действия
        box_actions = QGroupBox("Действия")
        act = QVBoxLayout(box_actions)

        finger_row = QHBoxLayout()
        finger_row.addWidget(QLabel("Палец:"))
        self.finger_combo = QComboBox()
        for code, name in FINGERS:
            self.finger_combo.addItem(name, code)
        finger_row.addWidget(self.finger_combo, 1)
        act.addLayout(finger_row)

        btn_row = QHBoxLayout()
        self.btn_enroll = QPushButton("Записать отпечаток")
        self.btn_verify = QPushButton("Проверить")
        self.btn_delete = QPushButton("Удалить все")
        for b in (self.btn_enroll, self.btn_verify, self.btn_delete):
            btn_row.addWidget(b)
        act.addLayout(btn_row)

        self.btn_enroll.clicked.connect(self.start_enroll)
        self.btn_verify.clicked.connect(self.start_verify)
        self.btn_delete.clicked.connect(self.delete_all)

        reset_row = QHBoxLayout()
        self.btn_reset = QPushButton("Сбросить сенсор")
        self.btn_reset.setToolTip(
            "Перезапускает модуль fte3600. Нужно, если сенсор перестал реагировать "
            "после предыдущей операции."
        )
        self.btn_reset.clicked.connect(lambda: self.reset_sensor(then=None))
        reset_row.addWidget(self.btn_reset)

        self.chk_auto = QCheckBox("Автосброс перед записью/проверкой")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(lambda v: setattr(self, "auto_reset", v))
        reset_row.addWidget(self.chk_auto)
        reset_row.addStretch(1)
        act.addLayout(reset_row)
        root.addWidget(box_actions)

        # Прогресс
        self.status = QLabel("Готово к работе")
        self.status.setWordWrap(True)
        f = self.status.font()
        f.setPointSize(f.pointSize() + 1)
        self.status.setFont(f)
        root.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # Журнал
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFont(QFont("monospace", 9))
        self.log.setFrameShape(QFrame.Shape.StyledPanel)
        self.log.setFixedHeight(140)
        root.addWidget(self.log)

        self.cancel_btn = QPushButton("Отменить операцию")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        root.addWidget(self.cancel_btn)

    # ---------- helpers ----------
    def add_log(self, text):
        self.log.appendPlainText(text.rstrip())

    def set_busy(self, busy):
        for b in (self.btn_enroll, self.btn_verify, self.btn_delete,
                  self.btn_reset, self.btn_refresh):
            b.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)

    def refresh_state(self):
        device, fingers = read_state()
        if device:
            self.device_label.setText(f"Устройство: {device}")
        else:
            self.device_label.setText(
                "Устройство: не найдено (нет прав или сенсор не подключён)"
            )
        if fingers:
            names = [FINGER_NAMES.get(f, f) for f in fingers]
            self.enrolled_label.setText("Записанные пальцы: " + ", ".join(names))
        else:
            self.enrolled_label.setText("Записанные пальцы: нет")

    def selected_finger(self):
        return self.finger_combo.currentData()

    def start_process(self, args, phase):
        self.phase = phase
        self.enroll_count = 0
        self.progress.setValue(0)
        self.set_busy(True)

        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(run_env())
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.errorOccurred.connect(self.on_error)

        self.add_log("$ " + " ".join(args))
        self.proc.start(args[0], args[1:])

    # ---------- operations ----------
    def reset_sensor(self, then):
        if not shutil.which("systemctl"):
            QMessageBox.warning(self, "Сброс", "Не найден systemctl.")
            return
        self.status.setText("Сброс сенсора…")
        self.progress.setValue(0)
        self.set_busy(True)
        self.pending_after_reset = then

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_reset_finished)
        self.proc.errorOccurred.connect(self.on_error)
        args = ["systemctl", "start", RESET_SERVICE]
        self.add_log("$ " + " ".join(args))
        self.proc.start(args[0], args[1:])

    def on_reset_finished(self, code, _status):
        self.add_log(f"[сброс завершён, код {code}]")
        self.set_busy(False)
        self.refresh_state()
        if code == 0:
            self.status.setText("Сенсор сброшен")
        else:
            self.status.setText("Не удалось сбросить сенсор (см. журнал)")
        then = getattr(self, "pending_after_reset", None)
        self.pending_after_reset = None
        if code == 0 and then:
            then()

    def start_enroll(self):
        finger = self.selected_finger()
        user = os.environ.get("USER", "")
        self.status.setText("Приложите палец к сканеру…")
        args = ["fprintd-enroll", "-f", finger, user]
        if self.auto_reset:
            self.reset_sensor(then=lambda: self.start_process(args, "enroll"))
        else:
            self.start_process(args, "enroll")

    def start_verify(self):
        finger = self.selected_finger()
        user = os.environ.get("USER", "")
        self.status.setText("Приложите палец к сканеру…")
        args = ["fprintd-verify", "-f", finger, user]
        if self.auto_reset:
            self.reset_sensor(then=lambda: self.start_process(args, "verify"))
        else:
            self.start_process(args, "verify")

    def delete_all(self):
        res = QMessageBox.question(
            self,
            "Удалить отпечатки",
            "Удалить все записанные отпечатки этого пользователя?",
        )
        if res != QMessageBox.StandardButton.Yes:
            return
        self.status.setText("Удаление…")
        self.start_process(["fprintd-delete", os.environ.get("USER", "")], "delete")

    def cancel(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self.add_log("[операция отменена]")

    # ---------- output parsing ----------
    def on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.add_log(line)
                self.handle_line(line)

    def handle_line(self, line):
        low = line.lower()

        if "enroll-stage-passed" in low:
            self.enroll_count += 1
            pct = int(self.enroll_count * 100 / ENROLL_SAMPLES)
            self.progress.setValue(pct)
            if self.enroll_count < ENROLL_SAMPLES:
                self.status.setText(
                    f"Принято {self.enroll_count} из {ENROLL_SAMPLES}. "
                    "Уберите палец и приложите снова."
                )
            else:
                self.status.setText("Запись завершается…")

        elif "enroll-retry-scan" in low:
            self.status.setText("Снимок не принят. Приложите палец ещё раз.")

        elif "enroll-duplicate" in low:
            self.status.setText("Такой снимок уже был. Сдвиньте палец и приложите снова.")

        elif "enroll-inconsistent" in low:
            self.status.setText("Положение пальца сильно отличается. Приложите ближе к центру.")

        elif "enroll-completed" in low:
            self.progress.setValue(100)
            self.status.setText("Готово: отпечаток записан.")

        elif "verify-match" in low:
            self.progress.setValue(100)
            self.status.setText("Совпадение: палец распознан.")

        elif "verify-no-match" in low:
            self.progress.setValue(0)
            self.status.setText("Не совпало. Попробуйте ещё раз.")

        elif "verify-unknown-error" in low or "timed out" in low:
            self.progress.setValue(0)
            self.status.setText(
                "Сенсор не увидел палец. Нажмите «Сбросить сенсор» и повторите."
            )

        elif "permissiondenied" in low or "not authorized" in low:
            self.status.setText(
                "Нет прав: утилиту нужно запускать в локальной сессии (не по SSH)."
            )

    def on_finished(self, code, _status):
        self.add_log(f"[команда завершена, код {code}]")
        self.set_busy(False)
        self.refresh_state()

    def on_error(self, err):
        self.add_log(f"[ошибка процесса: {err}]")
        self.set_busy(False)


def cli():
    if len(sys.argv) > 1 and sys.argv[1] in ("--list", "-l"):
        device, fingers = read_state()
        print("device:", device or "не найдено")
        print("fingers:", ", ".join(fingers) or "нет")
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r"):
        rc, out = run_sync(["systemctl", "start", RESET_SERVICE], timeout=60)
        print(out.strip())
        print("reset rc:", rc)
        return rc
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        return 0
    return None


def main():
    rc = cli()
    if rc is not None:
        return rc
    app = QApplication(sys.argv)
    app.setApplicationName("FTE3600 Fingerprint")
    win = FingerprintWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
