# -*- coding: utf-8 -*-
"""Окна настроек и диагностики сети."""
from __future__ import annotations

import datetime
import io
import os

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QColor, QDesktopServices
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
                             QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.config import save_user_config


def _path_row(edit: QLineEdit, folder: bool, parent, filt: str = "") -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit)
    b = QPushButton("…")
    b.setFixedWidth(30)

    def pick():
        if folder:
            p = QFileDialog.getExistingDirectory(parent, "Выберите папку", edit.text())
        else:
            p = QFileDialog.getOpenFileName(parent, "Выберите файл", edit.text(), filt)[0]
        if p:
            edit.setText(os.path.normpath(p))
    b.clicked.connect(pick)
    lay.addWidget(b)
    return w


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        cfg = ctx.config
        self.setWindowTitle("Настройки")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        ocr = cfg["module_settings"].get("ocr", {})
        self.tess = QLineEdit(ocr.get("tesseract_cmd", ""))
        self.tess.setPlaceholderText(r"C:\Program Files\Tesseract-OCR\tesseract.exe (пусто = искать автоматически)")
        form.addRow("Tesseract:", _path_row(self.tess, False, self, "tesseract.exe (tesseract.exe)"))

        self.roots = QListWidget()
        self.roots.addItems(cfg["paths"].get("scan_roots", []))
        self.roots.setMaximumHeight(110)
        rb = QWidget()
        rl = QHBoxLayout(rb)
        rl.setContentsMargins(0, 0, 0, 0)
        add = QPushButton("Добавить папку…")
        rem = QPushButton("Убрать")
        add_unc = QPushButton("Добавить сетевой путь…")
        rl.addWidget(add)
        rl.addWidget(add_unc)
        rl.addWidget(rem)
        rl.addStretch()
        add.clicked.connect(self._add_root)
        add_unc.clicked.connect(self._add_unc)
        rem.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        form.addRow("Папки с datasheet\n(сетевые диски, только чтение):", self.roots)
        form.addRow("", rb)

        self.library = QLineEdit(cfg["paths"].get("library_dir", ""))
        form.addRow("Папка для скачанных datasheet\n(ваша новая база):", _path_row(self.library, True, self))
        self.db = QLineEdit(cfg["paths"].get("db", ""))
        form.addRow("Файл индекса (SQLite):", _path_row(self.db, False, self, "SQLite (*.sqlite *.db)"))

        net = cfg.get("network", {})
        self.offline = QCheckBox("Автономный режим (не выходить в интернет)")
        self.offline.setChecked(bool(net.get("offline")))
        form.addRow("Сеть:", self.offline)
        self.proxy = QLineEdit(net.get("proxy", ""))
        self.proxy.setPlaceholderText("http://логин:пароль@proxy:3128 (пусто = системные настройки)")
        form.addRow("Прокси:", self.proxy)
        self.maxpdf = QSpinBox()
        self.maxpdf.setRange(1, 500)
        self.maxpdf.setValue(int(net.get("max_pdf_mb", 40)))
        self.maxpdf.setSuffix(" МБ")
        form.addRow("Макс. размер PDF:", self.maxpdf)

        pipe = cfg.get("pipeline", {})
        self.auto_web = QCheckBox("Сразу искать в интернете, если в базе нет")
        self.auto_web.setChecked(bool(pipe.get("auto_web_search")))
        self.auto_dl = QCheckBox("Автоматически скачивать и проверять найденные PDF")
        self.auto_dl.setChecked(bool(pipe.get("auto_download", True)))
        form.addRow("Поиск:", self.auto_web)
        form.addRow("", self.auto_dl)

        adv = QHBoxLayout()
        for title, rel in (("Открыть sources.json (источники поиска)", "data/sources.json"),
                           ("Открыть part_rules.json (семейства чипов)", "data/part_rules.json"),
                           ("Открыть config.json", "config.json")):
            b = QPushButton(title)
            b.clicked.connect(lambda _=False, r=rel: self._open_file(r))
            adv.addWidget(b)
        lay.addLayout(adv)
        lay.addWidget(QLabel("Изменения в .json-файлах вступают в силу после перезапуска программы."))

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Сохранить")
        bb.button(QDialogButtonBox.Cancel).setText("Отмена")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_root(self):
        p = QFileDialog.getExistingDirectory(self, "Папка с datasheet")
        if p:
            self.roots.addItem(os.path.normpath(p))

    def _add_unc(self):
        from PyQt5.QtWidgets import QInputDialog
        p, ok = QInputDialog.getText(self, "Сетевой путь", r"Например \\server\share\datasheets или Z:\datasheets")
        if ok and p.strip():
            self.roots.addItem(p.strip())

    def _open_file(self, rel):
        path = os.path.join(self.ctx.app_dir, rel)
        if not os.path.exists(path):
            with io.open(path, "w", encoding="utf-8") as f:
                f.write("{\n}\n")
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _save(self):
        user = {
            "paths": {"scan_roots": [self.roots.item(i).text() for i in range(self.roots.count())],
                      "library_dir": self.library.text().strip() or "data/library",
                      "db": self.db.text().strip() or "data/chipfinder.sqlite"},
            "module_settings": {"ocr": {"tesseract_cmd": self.tess.text().strip()}},
            "network": {"offline": self.offline.isChecked(), "proxy": self.proxy.text().strip(),
                        "max_pdf_mb": self.maxpdf.value()},
            "pipeline": {"auto_web_search": self.auto_web.isChecked(), "auto_download": self.auto_dl.isChecked()},
        }
        # scan_roots — список: сохраняем целиком, а не сливаем
        path = os.path.join(self.ctx.app_dir, "config.json")
        if os.path.exists(path):
            from ..core.config import read_json, write_json
            cur = read_json(path)
            cur.setdefault("paths", {}).pop("scan_roots", None)
            write_json(path, cur)
        save_user_config(self.ctx.app_dir, user)
        self.accept()


class DiagnosticsDialog(QDialog):
    """Показывает, какие сайты доступны с этого компьютера, и готовит список для администраторов."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle("Диагностика доступа к сайтам")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        ok = sum(1 for r in rows if r["ok"])
        lay.addWidget(QLabel("Доступно %d из %d. Зелёные — работают, красные — закрыты или не отвечают." % (ok, len(rows))))
        t = QTableWidget(len(rows), 4)
        t.setHorizontalHeaderLabels(["Тип", "Источник", "Результат", "Адрес"])
        for i, r in enumerate(rows):
            vals = [r["category"], r["name"], ("✔ " if r["ok"] else "✘ ") + r["detail"], r["url"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(QColor("#1a7f37") if r["ok"] else QColor("#c62828"))
                t.setItem(i, j, it)
        t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        t.resizeColumnsToContents()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(t)
        h = QHBoxLayout()
        save = QPushButton("Сохранить список закрытых сайтов для администраторов…")
        save.clicked.connect(self._save)
        h.addWidget(save)
        h.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        h.addWidget(close)
        lay.addLayout(h)

    def _save(self):
        p = QFileDialog.getSaveFileName(self, "Сохранить", "запрос_доступа_ChipFinder.txt", "Текст (*.txt)")[0]
        if not p:
            return
        blocked = sorted(set(r["domain"] for r in self.rows if not r["ok"]))
        allowed = sorted(set(r["domain"] for r in self.rows if r["ok"]))
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("Запрос на доступ для программы ChipFinder (поиск технической документации на микросхемы)\n")
            f.write("Дата проверки: %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            f.write("Программа обращается только к перечисленным доменам по HTTPS (порт 443), только чтение\n"
                    "страниц поиска и скачивание PDF. Все обращения пишутся в logs/network_audit.log.\n\n")
            f.write("НЕДОСТУПНЫ, прошу открыть (%d):\n" % len(blocked))
            for d in blocked:
                f.write("  %s\n" % d)
            f.write("\nУже доступны (%d):\n" % len(allowed))
            for d in allowed:
                f.write("  %s\n" % d)
        QMessageBox.information(self, "Готово", "Список сохранён:\n%s" % p)


ADAPTER_STATUS = {
    "ok": ("✔ доступен", "#1a7f37"), "empty": ("○ пусто", "#9a6700"), "captcha": ("✘ капча", "#c62828"),
    "no_key": ("— нет ключа", "#6e7781"), "quota": ("✘ лимит запросов", "#c62828"),
    "error": ("✘ ошибка сети", "#c62828"), "parse_error": ("✘ ошибка разбора", "#c62828"),
    "no_adapter": ("✘ нет адаптера", "#c62828"), "disabled": ("— выключен", "#6e7781"),
    "not_applicable": ("— не для тестовых чипов", "#6e7781"),
}


class AdaptersDialog(QDialog):
    """Результат диагностики по адаптерам: тестовый запрос (NE555) к каждому источнику."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        from ..acquire.diagnose import summary
        self.rows = rows
        self.setWindowTitle("Диагностика адаптеров")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        st = summary(rows)
        lay.addWidget(QLabel("Тестовый запрос NE555. Доступно %d из %d; остальное — см. столбец «Результат»."
                             % (st.get("ok", 0), len(rows))))
        t = QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Уровень", "Источник", "Результат", "Найдено", "Подробности"])
        for i, r in enumerate(rows):
            text, color = ADAPTER_STATUS.get(r["status"], (r["status"], "#c62828"))
            vals = [r["level"], r["name"], text, "%d (PDF %d)" % (r["leads"], r["pdfs"]), r["detail"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(QColor(color))
                t.setItem(i, j, it)
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        t.resizeColumnsToContents()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(t)
        h = QHBoxLayout()
        save = QPushButton("Сохранить список закрытых сайтов для администраторов…")
        save.clicked.connect(self._save)
        h.addWidget(save)
        h.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        h.addWidget(close)
        lay.addLayout(h)

    def _save(self):
        from ..acquire.diagnose import admin_domains
        p = QFileDialog.getSaveFileName(self, "Сохранить", "запрос_доступа_ChipFinder.txt", "Текст (*.txt)")[0]
        if not p:
            return
        blocked = admin_domains(self.rows)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("Запрос на доступ для программы ChipFinder (поиск технической документации на микросхемы)\n")
            f.write("Дата проверки: %s\n\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            f.write("Программа обращается только к перечисленным доменам по HTTPS (порт 443), только чтение\n"
                    "страниц поиска и скачивание PDF. Все обращения пишутся в logs/network_audit.log.\n\n")
            f.write("НЕДОСТУПНЫ, прошу открыть (%d):\n" % len(blocked))
            for d in blocked:
                f.write("  %s\n" % d)
        QMessageBox.information(self, "Готово", "Список сохранён:\n%s" % p)
