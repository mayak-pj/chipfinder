# -*- coding: utf-8 -*-
"""Диагностика по адаптерам (шаг 2.15)."""
import json
import logging
import os

from chipfinder.acquire import diagnose
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = "https://api.github.com/search/repositories?q=NE555+in:name,description,readme&per_page=10"
DATA = {"version": 2, "levels": [{"id": "github"}], "sources": [
    {"id": "gh", "adapter": "github_api", "name": "GitHub", "level": "github", "lang": "en", "url": GH.replace("NE555", "{part}"),
     "domains": ["api.github.com"]},
    {"id": "g", "adapter": "google_api", "name": "Google", "level": "github", "lang": "en", "needs_key": "google_api",
     "domains": ["googleapis.com"]},
    {"id": "q", "adapter": "engine_queries", "name": "Q", "level": "github", "lang": "en", "domains": []},
    {"id": "off", "adapter": "github_api", "name": "Off", "level": "github", "enabled": False, "domains": ["x.com"]},
]}


def make(tmp_path):
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["api.github.com"])
    return http, fake


def run(tmp_path, **kw):
    http, fake = make(tmp_path)
    return diagnose.diagnose_adapters(None, http, data=DATA, **kw), fake


def test_statuses(tmp_path):
    http, fake = make(tmp_path)
    fake.add(GH, json.dumps({"items": [{"html_url": "https://github.com/a/b", "full_name": "a/b"}]}),
             content_type="application/json")
    rows = {r["id"]: r for r in diagnose.diagnose_adapters(None, http, data=DATA)}
    assert rows["gh"]["status"] == "ok" and rows["gh"]["leads"] == 1
    assert rows["g"]["status"] == "no_key"
    assert rows["q"]["status"] == "no_adapter"
    assert rows["off"]["status"] == "disabled"
    assert diagnose.summary(list(rows.values())) == {"ok": 1, "no_key": 1, "no_adapter": 1, "disabled": 1}


def test_empty_error_quota_and_parse_error(tmp_path):
    http, fake = make(tmp_path)
    fake.add(GH, json.dumps({"items": []}), content_type="application/json")
    assert diagnose.diagnose_adapters(None, http, data=DATA)[0]["status"] == "empty"
    http, fake = make(tmp_path)
    fake.add(GH, "{}", status=403, content_type="application/json")
    assert diagnose.diagnose_adapters(None, http, data=DATA)[0]["status"] == "quota"
    http, fake = make(tmp_path)
    fake.add(GH, "{}", status=500, content_type="application/json")
    row = diagnose.diagnose_adapters(None, http, data=DATA)[0]
    assert row["status"] == "error" and row["detail"]
    assert diagnose.admin_domains([row]) == ["api.github.com"]
    http, fake = make(tmp_path)
    fake.add(GH, json.dumps({"items": [5]}), content_type="application/json")      # вёрстка не та: адаптер падает
    assert diagnose.diagnose_adapters(None, http, data=DATA)[0]["status"] == "parse_error"


def test_records_bad_responses_only(tmp_path):
    http, fake = make(tmp_path)
    fake.add(GH, json.dumps({"items": []}), content_type="application/json")
    rec = tmp_path / "rec"
    row = diagnose.diagnose_adapters(None, http, data=DATA, record_dir=str(rec))[0]
    assert row["saved"][0]["url"] == GH and (rec / row["saved"][0]["file"]).read_bytes().startswith(b'{"items"')


def test_real_sources_json_every_source_has_a_row(tmp_path):
    http, fake = make(tmp_path)
    rows = diagnose.diagnose_adapters(os.path.join(APP, "data", "sources.json"), http)
    data = json.load(open(os.path.join(APP, "data", "sources.json"), encoding="utf-8"))
    assert sorted(r["id"] for r in rows) == sorted(s["id"] for s in data["sources"])
    assert all(r["status"] in diagnose.STATUSES for r in rows)


def test_cancel_stops(tmp_path):
    class C:
        cancelled = True
    http, fake = make(tmp_path)
    assert diagnose.diagnose_adapters(None, http, data=DATA, cancel=C()) == []


def test_adapters_dialog_offscreen(tmp_path):
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    from chipfinder.gui.dialogs import ADAPTER_STATUS, AdaptersDialog
    assert set(diagnose.STATUSES) <= set(ADAPTER_STATUS)
    app = QApplication.instance() or QApplication([])
    rows, _ = run(tmp_path)
    dlg = AdaptersDialog(rows)
    from PyQt5.QtWidgets import QTableWidget
    assert dlg.findChild(QTableWidget).rowCount() == len(rows) and app is not None
