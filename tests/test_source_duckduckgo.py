# -*- coding: utf-8 -*-
"""Адаптер DuckDuckGo HTML (шаг 2.2): раскодирование uddg, капча, события."""
import logging
import os

from digger.acquire.events import KEYS, EventBus
from digger.acquire.query import Query
from digger.acquire.registry import ADAPTERS, Registry
from digger.acquire.sources.base import SourceEntry
from digger.acquire.sources.engine_html import EngineHtml, ddg_target
from digger.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join("sources", "duckduckgo")
ENTRY = {"id": "duckduckgo", "adapter": "engine_html", "name": "DuckDuckGo", "level": "search", "lang": "en",
         "url": "https://html.duckduckgo.com/html/?q={q}", "decoder": "ddg", "domains": ["duckduckgo.com"]}
URL = "https://html.duckduckgo.com/html/?q=STM32F103C8+datasheet+pdf"
Q = Query("en", "STM32F103C8 datasheet pdf", "part")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, entry=ENTRY):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["duckduckgo.com"])
    clock = Clock()
    return EngineHtml(SourceEntry.from_dict(entry), bus=bus, clock=clock), http, fake, events, clock


def test_registered_and_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    ddg = [a for a in reg.build() if a.id == "duckduckgo"]
    assert ADAPTERS["engine_html"] is EngineHtml and ddg and ddg[0].available


def test_ddg_target_decoding():
    assert ddg_target("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fx.pdf%3Fq%3D1&rut=z") == "https://a.com/x.pdf?q=1"
    assert ddg_target("https://example.com/p.html") == "https://example.com/p.html"
    assert ddg_target("//duckduckgo.com/y.js?ad_domain=shop.example") == ""      # реклама
    assert ddg_target("//duckduckgo.com/l/?uddg=javascript%3Aalert(1)") == ""
    assert ddg_target("#") == "" and ddg_target("") == ""


def test_parse_fixture(tmp_path):
    ad, http, fake, events, _ = make(tmp_path)
    fake.add_fixture(URL, os.path.join(FIX, "ok.html"))
    leads = ad.search(Q, http)
    assert [l.url for l in leads] == [
        "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
        "https://www.alldatasheet.com/datasheet-pdf/pdf/201596/STMICROELECTRONICS/STM32F103C8.html?a=1",
        "https://example.com/stm32f103c8.html"]
    assert [l.kind for l in leads] == ["pdf", "page", "page"]
    assert leads[0].title.startswith("STM32F103C8 - Datasheet") and "Cortex-M3" in leads[0].snippet
    assert "<b>" not in leads[0].snippet
    assert all(l.source_id == "duckduckgo" and l.language == "en" and l.level == "search" for l in leads)
    assert [e.key for e in events] == ["engine.query", "engine.found"]
    assert events[-1].params["n"] == 3 and events[-1].params["pdfs"] == 1


def test_empty(tmp_path):
    ad, http, fake, events, _ = make(tmp_path)
    fake.add_fixture(URL, os.path.join(FIX, "empty.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.empty"


def test_captcha_then_rest(tmp_path):
    ad, http, fake, events, clock = make(tmp_path)
    fake.add_fixture(URL, os.path.join(FIX, "captcha.html"), status=202)
    assert ad.search(Q, http) == []
    assert events[-1].key == "engine.captcha" and events[-1].params["minutes"] == 15
    assert KEYS["engine.captcha"] == "skip"
    n = len(fake.calls)
    clock.t += 10 * 60                       # домен отдыхает: в сеть не ходим
    assert ad.search(Q, http) == []
    assert len(fake.calls) == n and events[-1].key == "engine.captcha" and events[-1].params["minutes"] == 5
    clock.t += 10 * 60                       # отдых закончился
    fake.add_fixture(URL, os.path.join(FIX, "ok.html"))
    assert len(ad.search(Q, http)) == 3 and len(fake.calls) == n + 1


def test_http_errors(tmp_path):
    for status, key in ((429, "engine.quota"), (500, "engine.error")):
        ad, http, fake, events, _ = make(tmp_path)
        fake.add(URL, "x", status=status)
        assert ad.search(Q, http) == [] and events[-1].key == key


def test_unknown_decoder_is_error_event(tmp_path):
    ad, http, fake, events, _ = make(tmp_path, dict(ENTRY, decoder="nope"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.error" and fake.calls == []
