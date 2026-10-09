# -*- coding: utf-8 -*-
"""Адаптер Яндекса (шаг 2.5): разбор выдачи, пустая выдача, капча → отдых домена."""
import logging
import os

from digger.acquire.events import EventBus
from digger.acquire.query import Query
from digger.acquire.registry import Registry
from digger.acquire.sources.base import SourceEntry
from digger.acquire.sources.engine_html import EngineHtml, REST_MINUTES
from digger.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = {"id": "yandex", "adapter": "engine_html", "name": "Яндекс", "level": "russian", "lang": "ru",
         "url": "https://yandex.ru/search/?text={q}", "decoder": "yandex", "domains": ["yandex.ru"]}
Q = Query("ru", "STM32F103C8 datasheet pdf", "part")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["yandex.ru"])
    clock = Clock()
    ad = EngineHtml(SourceEntry.from_dict(ENTRY), bus=bus, clock=clock)
    return ad, http, fake, events, ENTRY["url"].format(q="STM32F103C8+datasheet+pdf"), clock


def test_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    ad = {a.id: a for a in reg.build()}["yandex"]
    assert ad.available and ad.entry.options["decoder"] == "yandex"


def test_parse_fixture(tmp_path):
    ad, http, fake, events, url, _ = make(tmp_path)
    fake.add_fixture(url, os.path.join("sources", "yandex", "ok.html"))
    leads = ad.search(Q, http)
    assert [l.url for l in leads] == ["https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
                                      "https://www.alldatasheet.com/a.html?x=1&y=2",
                                      "https://example.com/b.html"]  # yandex.ru отброшен
    assert [l.kind for l in leads] == ["pdf", "page", "page"]
    assert leads[0].title == "STM32F103C8 - Datasheet"
    assert "Cortex-M3" in leads[0].snippet and "<b>" not in leads[0].snippet
    assert leads[1].snippet == "Описание & аналоги."
    assert [e.key for e in events] == ["engine.query", "engine.found"]


def test_empty(tmp_path):
    ad, http, fake, events, url, _ = make(tmp_path)
    fake.add_fixture(url, os.path.join("sources", "yandex", "empty.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.empty"


def test_captcha_rest(tmp_path):
    ad, http, fake, events, url, clock = make(tmp_path)
    fake.add_fixture(url, os.path.join("sources", "yandex", "captcha.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
    n = len(fake.calls) if hasattr(fake, "calls") else None
    clock.t += 60
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
    if n is not None:
        assert len(fake.calls) == n  # во время отдыха в сеть не ходили
    clock.t += REST_MINUTES * 60
    fake.add_fixture(url, os.path.join("sources", "yandex", "ok.html"))
    assert len(ad.search(Q, http)) == 3
