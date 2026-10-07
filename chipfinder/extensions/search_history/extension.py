# -*- coding: utf-8 -*-
"""Расширение «История поисков» (шаг 7.9): итог каждого поиска (`result.*` с партномером) — в свою таблицу.

Подтверждённый документ повторно не ищется сам (оркестратор берёт его из библиотеки, acquire/orchestrator.py,
`_local`); «Искать снова» запускает поиск «везде» — по просьбе пользователя."""
from __future__ import annotations

import time

from chipfinder.extensions.api import Extension as BaseExtension

COLUMNS = ("Партномер", "Итог", "Источник", "Поисков", "Последний раз", "Секунд")
STATUS_TEXT = {"confirmed": "подтверждён", "probable": "вероятно", "needs_user": "нужно решение",
               "rejected": "отклонено", "not_found": "не найдено"}
FOUND = ("confirmed", "probable", "needs_user")
MIGRATIONS = ["CREATE TABLE {prefix}history (part TEXT PRIMARY KEY, status TEXT NOT NULL, source TEXT DEFAULT '', "
              "searches INTEGER NOT NULL DEFAULT 1, first_at REAL, last_at REAL, seconds INTEGER DEFAULT 0)"]


def _stamp(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


def record(db, part, status, source="", seconds=0, now=None):
    """Итог поиска. Подтверждённое не понижается ответом «не найдено» (например, поиск с урезанным списком)."""
    now = time.time() if now is None else now
    old = db.execute("SELECT status FROM {prefix}history WHERE part = ?", (part,))
    if not old:
        db.execute("INSERT INTO {prefix}history (part, status, source, searches, first_at, last_at, seconds) "
                   "VALUES (?, ?, ?, 1, ?, ?, ?)", (part, status, source, now, now, seconds))
    elif old[0][0] == "confirmed" and status != "confirmed":
        db.execute("UPDATE {prefix}history SET searches = searches + 1, last_at = ? WHERE part = ?", (now, part))
    else:
        db.execute("UPDATE {prefix}history SET status = ?, source = ?, searches = searches + 1, last_at = ?, "
                   "seconds = ? WHERE part = ?", (status, source, now, seconds, part))


def entries(db, text="", group=""):
    """Строки истории: `text` — часть партномера, `group` — "found" / "not_found" / "".
    Служебные символы LIKE в тексте экранируются."""
    sql, args = "SELECT part, status, source, searches, last_at, seconds FROM {prefix}history", []
    where = []
    if text.strip():
        where.append("UPPER(part) LIKE ? ESCAPE '\\'")
        args.append("%" + text.strip().upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if group == "found":
        where.append("status IN ('confirmed', 'probable', 'needs_user')")
    elif group == "not_found":
        where.append("status IN ('not_found', 'rejected')")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.execute(sql + " ORDER BY last_at DESC, part", args)


def table_rows(rows):
    return [[r[0], STATUS_TEXT.get(r[1], r[1]), r[2] or "", str(r[3]), _stamp(r[4]), str(r[5] or "")] for r in rows]


def summary(db):
    rows = db.execute("SELECT status, COUNT(*) FROM {prefix}history GROUP BY status")
    n = dict(rows)
    found = sum(n.get(s, 0) for s in FOUND)
    return "В истории %d: найдено %d, не найдено %d" % (sum(n.values()), found, sum(n.values()) - found)


class Extension(BaseExtension):
    def setup(self, services):
        self.services = services
        self.view = None
        self.dirty = False
        self.db = services.db()
        self.db.migrate(MIGRATIONS)
        services.subscribe(self.on_event)

    def on_event(self, event):
        key = event.key
        if not key.startswith("result."):
            return
        status, part = key[len("result."):], str(event.params.get("part") or "")
        if part and status in STATUS_TEXT:
            record(self.db, part, status, event.source or "", int(event.params.get("seconds") or 0))
            self.dirty = True

    def search_again(self, part, done=None):
        """Поиск «везде» для партномера в фоне; итог запишет `on_event`."""
        orch = self.services.orchestrator
        if orch is None:
            return False
        from chipfinder.acquire.models import PhotoContext
        self.services.run_in_background(lambda: orch.search(PhotoContext(part=part), everywhere=True), done)
        return True

    def contribute(self, ui):
        ui.add_tab("История поисков", self.make_tab)

    def make_tab(self):
        from PyQt5.QtCore import QTimer, Qt
        from PyQt5.QtGui import QStandardItem, QStandardItemModel
        from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                                     QPushButton, QTableView, QVBoxLayout, QWidget)
        ext = self
        w = QWidget()
        w.model = QStandardItemModel(0, len(COLUMNS), w)
        w.model.setHorizontalHeaderLabels(list(COLUMNS))
        w.table = QTableView()
        w.table.setModel(w.model)
        w.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        w.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        w.table.setSelectionMode(QAbstractItemView.SingleSelection)
        w.table.verticalHeader().hide()
        w.info = QLabel()
        w.hint = QLabel("Подтверждённое повторно не ищется само. «Искать снова» — поиск по всем источникам.")
        w.hint.setWordWrap(True)
        w.search = QLineEdit()
        w.search.setPlaceholderText("Поиск по партномеру")
        w.search.setClearButtonEnabled(True)
        w.filter = QComboBox()
        for label, value in (("Все", ""), ("Найденные", "found"), ("Не найденные", "not_found")):
            w.filter.addItem(label, value)
        w.b_again = QPushButton("Искать снова")
        row = QHBoxLayout()
        for x in (w.search, w.filter, w.b_again):
            row.addWidget(x)
        lay = QVBoxLayout(w)
        for x in (w.info, w.hint):
            lay.addWidget(x)
        lay.addLayout(row)
        lay.addWidget(w.table, 1)

        def show():
            w.model.removeRows(0, w.model.rowCount())
            for cells in table_rows(entries(ext.db, w.search.text(), w.filter.currentData())):
                items = [QStandardItem(c) for c in cells]
                for i in (3, 5):
                    items[i].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                w.model.appendRow(items)
            w.table.resizeColumnsToContents()
            w.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            w.info.setText(summary(ext.db))
            buttons()

        def buttons():
            w.b_again.setEnabled(w.table.currentIndex().isValid())

        def again():
            idx = w.table.currentIndex()
            if not idx.isValid():
                return
            part = w.model.item(idx.row(), 0).text()
            w.b_again.setEnabled(False)
            if not ext.search_again(part, lambda _r: show()):
                buttons()

        def refresh():
            ext.dirty = False
            show()

        w.search.textChanged.connect(lambda *_: show())
        w.filter.currentIndexChanged.connect(lambda *_: show())
        w.b_again.clicked.connect(again)
        w.table.selectionModel().currentRowChanged.connect(lambda *_: buttons())
        w.timer = QTimer(w)
        w.timer.timeout.connect(lambda: refresh() if ext.dirty and w.isVisible() else None)
        w.timer.start(1500)
        w.reload = refresh
        self.view = w
        show()
        return w
