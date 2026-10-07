# -*- coding: utf-8 -*-
"""Расширение «Статистика поиска» (шаг 7.10): показ показателей `acquire/stats.py` (§4.10) и экспорт CSV.
Данные копит сам поиск (`StatsRecorder`); здесь только чтение и показ."""
from __future__ import annotations

import os

from chipfinder.extensions.api import Extension as BaseExtension

COLUMNS = ("Источник", "Разрез", "Попыток", "Успех", "Время до подтверждения", "Запросов на успех", "Капча", "Ошибки")
GROUPS = (("В целом", ""), ("По семействам", "family"), ("По языкам", "lang"), ("По производителям", "maker"))


def _pct(x):
    return "%d%%" % round(x * 100)


def table_rows(metrics):
    """Показатели `SearchStats.metrics()` → строки таблицы; нет успехов — «—»."""
    return [[m["source"], m["group"] or "", str(m["attempts"]), _pct(m["success_rate"]),
             "—" if m["avg_confirm_time"] is None else "%.1f с" % m["avg_confirm_time"],
             "—" if m["queries_per_success"] is None else "%.1f" % m["queries_per_success"],
             _pct(m["captcha_rate"]), _pct(m["error_rate"])] for m in metrics]


def summary(stats):
    return "Поисков записано: %d, из них дошло до источников: %d" % (stats.search_count(), stats.network_searches())


class Extension(BaseExtension):
    def setup(self, services):
        self.services = services
        self.view = None
        self.dirty = False
        services.subscribe(self.on_event)

    def on_event(self, event):
        if event.key.startswith("result.") or event.key == "search.cancelled":
            self.dirty = True

    def stats(self):
        orch = self.services.orchestrator
        rec = getattr(orch, "recorder", None) if orch is not None else None
        return getattr(rec, "stats", None)

    def contribute(self, ui):
        ui.add_tab("Статистика поиска", self.make_tab)

    def make_tab(self):
        from PyQt5.QtCore import QTimer, Qt
        from PyQt5.QtGui import QStandardItem, QStandardItemModel
        from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton,
                                     QTableView, QVBoxLayout, QWidget)
        ext = self
        w = QWidget()
        w.model = QStandardItemModel(0, len(COLUMNS), w)
        w.model.setHorizontalHeaderLabels(list(COLUMNS))
        w.table = QTableView()
        w.table.setModel(w.model)
        w.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        w.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        w.table.verticalHeader().hide()
        w.table.setSortingEnabled(False)
        w.info = QLabel("Статистики пока нет: она появится после первого поиска в интернете.")
        w.info.setWordWrap(True)
        w.group = QComboBox()
        for label, value in GROUPS:
            w.group.addItem(label, value)
        w.b_refresh = QPushButton("Обновить")
        w.b_csv = QPushButton("Сохранить CSV…")
        row = QHBoxLayout()
        for b in (w.group, w.b_refresh, w.b_csv):
            row.addWidget(b)
        row.addStretch()
        lay = QVBoxLayout(w)
        lay.addWidget(w.info)
        lay.addLayout(row)
        lay.addWidget(w.table, 1)

        def collect():
            st = ext.stats()
            if st is None:
                return None
            return summary(st), table_rows(st.metrics(by=w.group.currentData()))

        def done(res):
            w.model.removeRows(0, w.model.rowCount())
            if res is None:
                return
            text, rows = res
            for cells in rows:
                items = [QStandardItem(c) for c in cells]
                for i in range(2, len(cells)):
                    items[i].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                w.model.appendRow(items)
            w.table.resizeColumnsToContents()
            w.info.setText(text if rows else text + ". Таблица пуста: нужен хотя бы один поиск по источникам.")
            w.b_csv.setEnabled(bool(rows))

        def load():
            ext.dirty = False
            ext.services.run_in_background(collect, done)

        def save():
            st = ext.stats()
            if st is None:
                return
            path, _ = QFileDialog.getSaveFileName(
                w, "Сохранить статистику", os.path.join(ext.services.app_dir, "статистика_поиска.csv"))
            if path:
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(st.export_csv(by=w.group.currentData()))

        w.group.currentIndexChanged.connect(lambda *_: load())
        w.b_refresh.clicked.connect(load)
        w.b_csv.clicked.connect(save)
        w.timer = QTimer(w)
        w.timer.timeout.connect(lambda: load() if ext.dirty and w.isVisible() else None)
        w.timer.start(1500)
        w.reload = load
        self.view = w
        load()
        return w
