# -*- coding: utf-8 -*-
"""Диагностика по адаптерам (шаг 2.15)."""
import json
import logging
import os
import sys

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
    assert rows["q"]["status"] == "empty"        # нет поисковиков `via` → site.empty
    assert rows["off"]["status"] == "disabled"
    assert diagnose.summary(list(rows.values())) == {"ok": 1, "no_key": 1, "empty": 1, "disabled": 1}


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


MAKERS = {"version": 2, "levels": [{"id": "maker"}], "sources": [
    {"id": "esp", "adapter": "maker_url", "name": "Espressif", "level": "maker", "lang": "en",
     "domains": ["espressif.example"], "prefixes": ["^ESP"], "urls": ["https://espressif.example/ds/{part_lower}.pdf"]},
    {"id": "rare", "adapter": "maker_url", "name": "Rare", "level": "maker", "lang": "en",
     "domains": ["rare.example"], "prefixes": ["^ZZZ"], "urls": ["https://rare.example/{part}.pdf"]},
]}


def test_maker_gets_a_chip_it_takes_and_is_not_reported_as_blocked(tmp_path):
    """Выезд 2: сайты производителей молча пропускали NE555 и попадали в список «не отвечают»."""
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["espressif.example", "rare.example"])
    fake.add("https://espressif.example/ds/esp8266ex.pdf", b"%PDF-1.4 x", content_type="application/pdf")
    rows = {r["id"]: r for r in diagnose.diagnose_adapters(None, http, data=MAKERS)}
    assert rows["esp"]["status"] == "ok" and rows["esp"]["part"] == "ESP8266EX" and rows["esp"]["pdfs"] == 1
    assert rows["rare"]["status"] == "not_applicable" and rows["rare"]["detail"]
    assert diagnose.admin_domains(list(rows.values())) == []
    assert not rows["rare"]["saved"] and rows["rare"]["seconds"] == 0.0     # в сеть не ходили


def test_cancel_stops(tmp_path):
    class C:
        cancelled = True
    http, fake = make(tmp_path)
    assert diagnose.diagnose_adapters(None, http, data=DATA, cancel=C()) == []


def test_adapters_dialog_offscreen(tmp_path):
    import pytest
    if sys.platform != "win32":     # на Win7 модуль offscreen обрушил самопроверку (выезд 2); там окно — настоящее
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


def test_offtopic_engine_is_bad_but_not_blocked(tmp_path):
    """Выезд 2: поисковик отвечает, но выдача не по запросу — отдельный итог, в запрос администраторам не попадает."""
    data = {"version": 2, "levels": [{"id": "search"}], "sources": [
        {"id": "bing", "adapter": "engine_html", "name": "Bing", "level": "search", "lang": "en", "decoder": "bing",
         "url": "https://www.bing.com/search?q={q}", "domains": ["bing.com"]}]}
    http, fake = make(tmp_path)
    http.add_allowed(["bing.com"])
    fake.add_fixture("https://www.bing.com/search?q=NE555+datasheet+pdf", os.path.join("sources", "bing", "offtopic.html"))
    row = diagnose.diagnose_adapters(None, http, data=data, record_dir=str(tmp_path / "raw"))[0]
    assert row["status"] == "offtopic" and row["leads"] == 0 and "не по запросу" in row["detail"]
    assert row["saved"] and "offtopic" in diagnose.BAD and "offtopic" in diagnose.STATUSES
    assert diagnose.admin_domains([row]) == []
