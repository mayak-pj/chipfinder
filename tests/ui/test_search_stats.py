# -*- coding: utf-8 -*-
"""Шаг 7.10: расширение «Статистика поиска» — строки таблицы, разрезы, вкладка."""
import importlib.util
import logging
import os
import sys

import pytest

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.stats import SearchStats, StatsRecorder
from chipfinder.core.interfaces import Context
from chipfinder.extensions.loader import BUILTIN_DIR
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB

pytest.importorskip("PyQt5")


def _mod():
    spec = importlib.util.spec_from_file_location("ss_ext", os.path.join(BUILTIN_DIR, "search_stats", "extension.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _stats(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    stats = SearchStats(SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t"))))
    bus = EventBus()
    rec = StatsRecorder(stats, bus)
    for part, result, src, lang in (("W25Q64JV", "confirmed", "ddg", "en"), ("W25Q64JV", "not_found", "ddg", "en"),
                                    ("LM358", "not_found", "baidu", "zh")):
        rec.begin(part)
        bus.emit("engine.query", lang=lang, source=src, engine=src, query="q")
        bus.emit("engine.found", lang=lang, source=src, engine=src, query="q", n=2)
        if result == "confirmed":
            bus.emit("fetch.done", lang=lang, source=src, file="a.pdf", size=1)
        bus.emit("result." + result, queries=1, seconds=1)
    return stats, rec


def test_table_rows(tmp_path):
    m = _mod()
    stats, _ = _stats(tmp_path)
    rows = {r[0]: r for r in m.table_rows(stats.metrics())}
    assert rows["ddg"][2:4] == ["2", "50%"] and rows["ddg"][5] == "2.0"
    assert rows["baidu"][3] == "0%" and rows["baidu"][4] == "—" and rows["baidu"][5] == "—"
    assert {r[1] for r in m.table_rows(stats.metrics(by="lang"))} == {"en", "zh"}
    assert m.summary(stats) == "Поисков записано: 3, из них дошло до источников: 3"


def test_tab_in_window(window, monkeypatch, tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from test_latency import _pump
    w, app = window
    assert "Статистика поиска" in [w.tabs.tabText(i) for i in range(w.tabs.count())]
    inst = w.ext.get("search_stats").instance
    stats, rec = _stats(tmp_path)
    monkeypatch.setattr(inst, "stats", lambda: stats)
    view = inst.view
    view.reload()
    _pump(app, lambda: view.model.rowCount() == 2)
    assert view.b_csv.isEnabled() and "Поисков записано: 3" in view.info.text()
    view.group.setCurrentIndex(2)
    _pump(app, lambda: {view.model.item(i, 1).text() for i in range(view.model.rowCount())} == {"en", "zh"})
