# -*- coding: utf-8 -*-
"""Адаптер Google Custom Search JSON API (шаг 2.1)."""
import json
import logging
import os

from chipfinder.acquire.events import KEYS, LANGS, EventBus, render
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.google_api import GoogleApi
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = {"id": "google", "adapter": "google_api", "name": "Google", "level": "search", "lang": "en",
         "url": "https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={q}",
         "domains": ["googleapis.com"], "needs_key": "google_api"}
KEY = {"key": "AIza-test", "cx": "cx123"}
URL = "https://www.googleapis.com/customsearch/v1?key=AIza-test&cx=cx123&q=STM32F103C8+datasheet+pdf"


def make(tmp_path, key=KEY):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["googleapis.com"])
    return GoogleApi(SourceEntry.from_dict(ENTRY), bus=bus, key=key), http, fake, events


def test_registered_and_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"), keys={"google_api": KEY})
    google = [a for a in reg.build() if a.id == "google"]
    assert ADAPTERS["google_api"] is GoogleApi
    assert google and google[0].available and "google_api" not in reg.missing


def test_parse_fixture(tmp_path):
    ad, http, fake, events = make(tmp_path)
    with open(os.path.join(APP, "tests", "fixtures", "sources", "google_api", "ok.json"), "rb") as f:
        fake.add(URL, f.read(), content_type="application/json")
    leads = ad.search(Query("en", "STM32F103C8 datasheet pdf", "part"), http)
    assert [l.kind for l in leads] == ["pdf", "page", "pdf"]
    assert leads[0].url.endswith("stm32f103c8.pdf") and "Cortex-M3" in leads[0].snippet
    assert all(l.source_id == "google" and l.level == "search" and l.language == "en" for l in leads)
    assert [e.key for e in events] == ["engine.query", "engine.found"]
    assert events[-1].params["n"] == 3 and events[-1].params["pdfs"] == 2


def test_empty_result(tmp_path):
    ad, http, fake, events = make(tmp_path)
    with open(os.path.join(APP, "tests", "fixtures", "sources", "google_api", "empty.json"), "rb") as f:
        fake.add(URL, f.read(), content_type="application/json")
    assert ad.search(Query("en", "STM32F103C8 datasheet pdf", "part"), http) == []
    assert events[-1].key == "engine.empty"


def test_no_key_skips_without_request(tmp_path):
    for key in (None, {"key": "", "cx": ""}, {"key": "k", "cx": ""}):
        ad, http, fake, events = make(tmp_path, key=key)
        assert ad.search("STM32F103C8 datasheet pdf", http) == []
        assert fake.calls == [] and [e.key for e in events] == ["engine.no_key"]


def test_quota_and_http_errors_become_event(tmp_path):
    for status, key in ((429, "engine.quota"), (403, "engine.quota"), (400, "engine.error"), (500, "engine.error")):
        ad, http, fake, events = make(tmp_path)
        fake.add(URL, json.dumps({"error": {"code": status, "message": "x"}}), status=status,
                 content_type="application/json")
        assert ad.search("STM32F103C8 datasheet pdf", http) == []
        assert events[-1].key == key
        assert KEYS[events[-1].key] == "fail"


def test_bad_json_and_network_error(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add(URL, "<html>not json</html>")
    assert ad.search("STM32F103C8 datasheet pdf", http) == []
    assert events[-1].key == "engine.error"
    ad, http, fake, events = make(tmp_path)
    fake.add_error(URL, ConnectionError("down"))
    assert ad.search("STM32F103C8 datasheet pdf", http) == []
    assert events[-1].key == "engine.error"


def test_key_not_in_event_text_or_audit_log(tmp_path, caplog):
    ad, http, fake, events = make(tmp_path)
    fake.add(URL, "{}", content_type="application/json")
    with caplog.at_level(logging.INFO, logger="chipfinder.net"):
        ad.search("STM32F103C8 datasheet pdf", http)
    assert "googleapis.com" in caplog.text and "AIza-test" not in caplog.text
    for e in events:
        for lang in LANGS:
            assert "AIza-test" not in render(e, lang)


def test_error_key_in_all_dictionaries():
    for k in ("engine.quota", "engine.error"):
        assert KEYS[k] == "fail"
    for lang in LANGS:
        with open(os.path.join(APP, "data", "i18n", lang + ".json"), encoding="utf-8") as f:
            cat = json.load(f)
            assert "engine.quota" in cat and "engine.error" in cat
