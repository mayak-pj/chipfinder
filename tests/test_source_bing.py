# -*- coding: utf-8 -*-
"""Адаптеры Bing и Bing CN (шаг 2.3): раскодирование u=a1…, капча, события."""
import base64
import logging
import os

import pytest

from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import Registry
from chipfinder.acquire.events import EventBus
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.engine_html import EngineHtml, bing_target
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join("sources", "bing")
ENTRIES = {
    "bing": {"id": "bing", "adapter": "engine_html", "name": "Bing", "level": "search", "lang": "en",
             "url": "https://www.bing.com/search?q={q}&setlang=en", "decoder": "bing", "domains": ["bing.com"]},
    "bing_cn": {"id": "bing_cn", "adapter": "engine_html", "name": "必应", "level": "china", "lang": "zh",
                "url": "https://cn.bing.com/search?q={q}&ensearch=0", "decoder": "bing", "domains": ["cn.bing.com"]},
}
Q = Query("en", "STM32F103C8 datasheet pdf", "part")


def make(tmp_path, key="bing"):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["bing.com", "cn.bing.com"])
    url = ENTRIES[key]["url"].format(q="STM32F103C8+datasheet+pdf")
    return EngineHtml(SourceEntry.from_dict(ENTRIES[key]), bus=bus), http, fake, events, url


def test_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    ids = {a.id: a for a in reg.build()}
    assert ids["bing"].available and ids["bing_cn"].available


def test_bing_target_decoding():
    u = "https://a.com/x.pdf?q=1&b=2"
    enc = base64.urlsafe_b64encode(u.encode()).decode().rstrip("=")
    assert bing_target("https://www.bing.com/ck/a?!&amp;&amp;p=z&amp;u=a1%s&amp;ntb=1" % enc) == u
    assert bing_target("https://www.bing.com/ck/a?p=z&u=a1" + enc + "&ntb=1") == u
    assert bing_target("https://example.com/p.html") == "https://example.com/p.html"
    assert bing_target("https://www.bing.com/aclick?ld=x") == ""
    assert bing_target("https://www.bing.com/ck/a?u=a1!!!") == ""
    js = base64.urlsafe_b64encode(b"javascript:alert(1)").decode()
    assert bing_target("https://www.bing.com/ck/a?u=a1" + js) == ""
    assert bing_target("") == ""


@pytest.mark.parametrize("key", ["bing", "bing_cn"])
def test_parse_fixture(tmp_path, key):
    ad, http, fake, events, url = make(tmp_path, key)
    fake.add_fixture(url, os.path.join(FIX, "ok.html"))
    leads = ad.search(Q, http)
    assert [l.url for l in leads] == [
        "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
        "https://www.alldatasheet.com/datasheet-pdf/pdf/201596/STMICROELECTRONICS/STM32F103C8.html?a=1",
        "https://example.com/stm32f103c8.html"]
    assert [l.kind for l in leads] == ["pdf", "page", "page"]
    assert "Cortex-M3" in leads[0].snippet and "<strong>" not in leads[0].snippet
    assert leads[2].snippet == "Blue pill & more."
    assert all(l.source_id == key for l in leads)
    assert [e.key for e in events] == ["engine.query", "engine.found"]


def test_empty_and_captcha(tmp_path):
    ad, http, fake, events, url = make(tmp_path)
    fake.add_fixture(url, os.path.join(FIX, "empty.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.empty"
    fake.add_fixture(url, os.path.join(FIX, "captcha.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
