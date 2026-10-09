# -*- coding: utf-8 -*-
"""Адаптеры Mojeek и Brave (шаг 2.4): разбор выдачи, пустая выдача, капча."""
import logging
import os

import pytest

from digger.acquire.events import EventBus
from digger.acquire.query import Query
from digger.acquire.registry import Registry
from digger.acquire.sources.base import SourceEntry
from digger.acquire.sources.engine_html import EngineHtml
from digger.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRIES = {
    "mojeek": {"id": "mojeek", "adapter": "engine_html", "name": "Mojeek", "level": "search", "lang": "en",
               "url": "https://www.mojeek.com/search?q={q}", "decoder": "mojeek", "domains": ["mojeek.com"]},
    "brave": {"id": "brave", "adapter": "engine_html", "name": "Brave Search", "level": "search", "lang": "en",
              "url": "https://search.brave.com/search?q={q}", "decoder": "brave", "domains": ["search.brave.com"]},
}
Q = Query("en", "STM32F103C8 datasheet pdf", "part")


def make(tmp_path, key):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["mojeek.com", "search.brave.com"])
    url = ENTRIES[key]["url"].format(q="STM32F103C8+datasheet+pdf")
    return EngineHtml(SourceEntry.from_dict(ENTRIES[key]), bus=bus), http, fake, events, url


def test_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    ids = {a.id: a for a in reg.build()}
    assert ids["mojeek"].available and ids["brave"].available
    assert ids["mojeek"].entry.options["decoder"] == "mojeek"
    assert ids["brave"].entry.options["decoder"] == "brave"


@pytest.mark.parametrize("key", ["mojeek", "brave"])
def test_parse_fixture(tmp_path, key):
    ad, http, fake, events, url = make(tmp_path, key)
    fake.add_fixture(url, os.path.join("sources", key, "ok.html"))
    leads = ad.search(Q, http)
    urls = [l.url for l in leads]
    assert urls[0] == "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"
    assert urls[1] == "https://www.alldatasheet.com/a.html?x=1&y=2"
    assert urls[-1] == "https://example.com/b.html"
    assert len(urls) == 3  # внутренние ссылки brave.com отброшены
    assert [l.kind for l in leads] == ["pdf", "page", "page"]
    assert leads[0].title == "STM32F103C8 - Datasheet"
    assert "Cortex-M3" in leads[0].snippet and "<strong>" not in leads[0].snippet
    assert leads[1].snippet == "STM32F103C8 datasheet & equivalent."
    assert all(l.source_id == key for l in leads)
    assert [e.key for e in events] == ["engine.query", "engine.found"]


@pytest.mark.parametrize("key", ["mojeek", "brave"])
def test_empty_and_captcha(tmp_path, key):
    ad, http, fake, events, url = make(tmp_path, key)
    fake.add_fixture(url, os.path.join("sources", key, "empty.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.empty"
    fake.add_fixture(url, os.path.join("sources", key, "captcha.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
