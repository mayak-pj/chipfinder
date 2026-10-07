# -*- coding: utf-8 -*-
"""Расширение «Сайты без доступа» (шаг 7.8): накопленный список `network_blocks` (acquire/access.py), статусы,
экспорт «Запроса на доступ» для администраторов. Список ведёт поиск; здесь — только показ и экспорт."""
from __future__ import annotations

import os

from chipfinder.acquire.access import BLOCKED, OPEN, _stamp
from chipfinder.extensions.api import Extension as BaseExtension

COLUMNS = ("Сайт", "Статус", "Раз", "Микросхемы", "Последний раз", "Польза", "Пример")
STATUS_TEXT = {BLOCKED: "закрыт", OPEN: "доступ открыт"}


def table_rows(entries, status=""):
    """Записи `AccessLog.entries()` → строки таблицы (список из семи строк); `status` — фильтр."""
    rows = []
    for e in entries:
        if status and e["status"] != status:
            continue
        benefit = "" if e["benefit"] is None else "%d%%" % round(e["benefit"] * 100)
        rows.append([e["domain"], STATUS_TEXT.get(e["status"], e["status"]), str(e["attempts"]),
                     ", ".join(e["parts"][:5]) + (" …" if len(e["parts"]) > 5 else ""),
                     _stamp(e["last_at"]), benefit, e["sample_url"]])
    return rows


def summary(entries):
    closed = sum(1 for e in entries if e["status"] == BLOCKED)
    return "Закрыто сетью: %d · доступ открыт: %d" % (closed, len(entries) - closed)


class Extension(BaseExtension):
    def setup(self, services):
        self.services = services
        self.view = None
        self.dirty = False
        services.subscribe(self.on_event)

    def on_event(self, event):
        # поток поиска: только пометка; окно обновит себя по таймеру
        if event.key.startswith("access.") or event.key in ("fetch.done", "engine.found", "site.found"):
            self.dirty = True

    def access(self):
        orch = self.services.orchestrator
        return getattr(orch, "access", None) if orch is not None else None

    def contribute(self, ui):
        ui.add_tab("Сайты без доступа", self.make_tab)

    def make_tab(self):
        from PyQt5.QtCore import QTimer, Qt
        from PyQt5.QtGui import QStandardItem, QStandardItemModel
        from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                                     QLabel, QPushButton, QTableView, QVBoxLayout, QWidget)
        ext = self
        w = QWidget()
        w.entries = []
        w.model = QStandardItemModel(0, len(COLUMNS), w)
        w.model.setHorizontalHeaderLabels(list(COLUMNS))
        w.table = QTableView()
        w.table.setModel(w.model)
        w.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        w.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        w.table.verticalHeader().hide()
        w.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        w.info = QLabel()
        w.info.setWordWrap(True)
        w.filter = QComboBox()
        for label, value in (("Все", ""), ("Закрыты", BLOCKED), ("Доступ открыт", OPEN)):
            w.filter.addItem(label, value)
        w.b_refresh = QPushButton("Обновить")
        w.b_copy = QPushButton("Копировать запрос")
        w.b_txt = QPushButton("Сохранить запрос (текст)…")
        w.b_csv = QPushButton("Сохранить CSV…")
        row = QHBoxLayout()
        for b in (w.filter, w.b_refresh, w.b_copy, w.b_txt, w.b_csv):
            row.addWidget(b)
        row.addStretch()
        lay = QVBoxLayout(w)
        lay.addWidget(w.info)
        lay.addLayout(row)
        lay.addWidget(w.table, 1)

        def show():
            w.model.removeRows(0, w.model.rowCount())
            for cells in table_rows(w.entries, w.filter.currentData()):
                items = [QStandardItem(c) for c in cells]
                items[2].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                w.model.appendRow(items)
            w.table.resizeColumnsToContents()
            w.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
            w.info.setText(summary(w.entries) if w.entries else
                           "Список пуст: сайты появятся здесь, когда поиск не сможет до них достучаться.")
            has_blocked = any(e["status"] == BLOCKED for e in w.entries)
            for b in (w.b_copy, w.b_txt, w.b_csv):
                b.setEnabled(has_blocked)

        def load():
            ext.dirty = False
            log = ext.access()
            if log is not None:
                ext.services.run_in_background(log.entries, done)

        def done(entries):
            w.entries = entries or []
            show()

        def export(kind):
            log = ext.access()
            if log is None:
                return None
            return log.export_csv() if kind == "csv" else log.export_txt(os.path.join(ext.services.app_dir, "logs"))

        def save(kind):
            text = export(kind)
            if text is None:
                return
            name = "запрос_на_доступ." + ("csv" if kind == "csv" else "txt")
            path, _ = QFileDialog.getSaveFileName(w, "Сохранить запрос", os.path.join(ext.services.app_dir, name))
            if path:
                with open(path, "w", encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="") as f:
                    f.write(text)

        def copy():
            text = export("txt")
            if text:
                QApplication.clipboard().setText(text)

        w.filter.currentIndexChanged.connect(lambda *_: show())
        w.b_refresh.clicked.connect(load)
        w.b_copy.clicked.connect(copy)
        w.b_txt.clicked.connect(lambda: save("txt"))
        w.b_csv.clicked.connect(lambda: save("csv"))
        w.timer = QTimer(w)
        w.timer.timeout.connect(lambda: load() if ext.dirty and w.isVisible() else None)
        w.timer.start(1500)
        w.reload = load
        self.view = w
        load()
        return w
