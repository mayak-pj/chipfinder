# -*- coding: utf-8 -*-
"""Шаг 7.9: расширение «История поисков» — запись итогов, поиск по базе, «Искать снова», вкладка."""
import importlib.util
import os
import sys

import pytest

from digger.extensions.api import ExtensionDb
from digger.extensions.loader import BUILTIN_DIR

pytest.importorskip("PyQt5")


def _mod():
    spec = importlib.util.spec_from_file_location("sh_ext", os.path.join(BUILTIN_DIR, "search_history", "extension.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _db(m, tmp_path):
    db = ExtensionDb(str(tmp_path / "h.sqlite"), "search_history")
    db.migrate(m.MIGRATIONS)
    return db


def test_record_and_query(tmp_path):
    m = _mod()
    db = _db(m, tmp_path)
    m.record(db, "NE555P", "confirmed", "local", 3, now=100)
    m.record(db, "LM358", "not_found", "", 40, now=200)
    m.record(db, "NE555P", "not_found", "", 5, now=300)      # подтверждённое не понижается
    rows = m.entries(db)
    assert [r[0] for r in rows] == ["NE555P", "LM358"]
    assert rows[0][1] == "confirmed" and rows[0][3] == 2
    assert [r[0] for r in m.entries(db, "ne5")] == ["NE555P"]
    assert m.entries(db, "100%") == [] and m.entries(db, "_") == []
    assert [r[0] for r in m.entries(db, group="not_found")] == ["LM358"]
    assert m.summary(db) == "В истории 2: найдено 1, не найдено 1"
    assert m.table_rows(rows)[1][:2] == ["LM358", "не найдено"]


def test_tab_events_and_search_again(window, tmp_path, monkeypatch):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from test_latency import _pump
    w, app = window
    assert "История поисков" in [w.tabs.tabText(i) for i in range(w.tabs.count())]
    inst = w.ext.get("search_history").instance
    w.ctx.bus.emit("result.not_found", part="LM358", seconds=7, queries=3, sources=4)
    w.ctx.bus.emit("result.confirmed", part="NE555P", source="local", seconds=1)
    w.ctx.bus.emit("result.not_found", seconds=1)             # без партномера — не записывается
    view = inst.view
    view.reload()
    assert view.model.rowCount() == 2 and "найдено 1" in view.info.text()
    calls = []

    class Orch:
        def search(self, ctx, everywhere=False):
            calls.append((ctx.part, everywhere))
    monkeypatch.setattr(type(inst.services), "orchestrator", property(lambda self: Orch()))
    view.table.selectRow(0)
    view.b_again.click()
    _pump(app, lambda: calls)
    assert calls and calls[0][1] is True
