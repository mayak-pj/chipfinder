# -*- coding: utf-8 -*-
"""Шаг 7.8: расширение «Сайты без доступа» — строки таблицы, сводка, вкладка и экспорт запроса."""
import importlib.util
import logging
import os

import pytest

from chipfinder.acquire.access import AccessLog
from chipfinder.core.interfaces import Context
from chipfinder.extensions.loader import BUILTIN_DIR
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB

pytest.importorskip("PyQt5")


def _mod():
    spec = importlib.util.spec_from_file_location("bs_ext", os.path.join(BUILTIN_DIR, "blocked_sites", "extension.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _log(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    log = AccessLog(SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t"))))
    log.record_failure("ti.com", "network_blocked", "NE555P", "https://www.ti.com/x", "maker", 0.4)
    log.record_failure("ti.com", "network_blocked", "LM358")
    log.record_failure("st.com", "network_blocked", "L7805")
    log.record_ok("st.com")
    return log


def test_rows_and_summary(tmp_path):
    m, log = _mod(), _log(tmp_path)
    entries = log.entries()
    rows = m.table_rows(entries)
    assert rows[0][:3] == ["ti.com", "закрыт", "2"] and rows[0][3] == "LM358, NE555P" and rows[0][5] == "40%"
    assert rows[1][:2] == ["st.com", "доступ открыт"]
    assert [r[0] for r in m.table_rows(entries, "open")] == ["st.com"]
    assert m.summary(entries) == "Закрыто сетью: 1 · доступ открыт: 1"
    assert "ti.com" in log.export_txt() and "st.com" not in log.export_csv()


def test_tab_in_window_shows_list(window, tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from test_latency import _pump
    w, app = window
    names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert "Сайты без доступа" in names
    inst = w.ext.get("blocked_sites").instance
    inst.access = lambda: _log(tmp_path)
    view = inst.view
    view.reload()
    _pump(app, lambda: view.model.rowCount() == 2)
    assert view.b_txt.isEnabled() and "Закрыто сетью: 1" in view.info.text()
    view.filter.setCurrentIndex(2)
    assert view.model.rowCount() == 1
