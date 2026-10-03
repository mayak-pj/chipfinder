# -*- coding: utf-8 -*-
"""Адаптеры Baidu, Sogou, 360 (шаг 2.6): разбор выдачи, пустая выдача, капча → отдых, GB18030."""
import logging
import os

import pytest

from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import Registry
from chipfinder.acquire.sources.engine_html import EngineHtml, REST_MINUTES
from chipfinder.acquire.events import EventBus
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = Query("zh", "STM32F103C8 datasheet pdf", "part")
PDF = "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"
# id -> (домен, ожидаемые адреса)
CASES = {
    "baidu": ("baidu.com", [PDF, "https://www.21ic.com/a/1.html"]),
    "sogou": ("sogou.com", [PDF, "https://www.eeworld.com.cn/a.html"]),
    "so360": ("so.com", [PDF, "https://www.51hei.com/b.html"]),
}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, eid):
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    entry = [e for e in reg.entries() if e.id == eid][0]
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed([CASES[eid][0]])
    clock = Clock()
    ad = EngineHtml(entry, bus=bus, clock=clock)
    return ad, http, fake, events, entry.options["url"].format(q="STM32F103C8+datasheet+pdf"), clock


@pytest.mark.parametrize("eid", sorted(CASES))
def test_in_sources_json(eid):
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    ad = {a.id: a for a in reg.build()}[eid]
    assert ad.available and ad.entry.options["decoder"] == eid


@pytest.mark.parametrize("eid", sorted(CASES))
def test_parse_fixture(tmp_path, eid):
    ad, http, fake, events, url, _ = make(tmp_path, eid)
    fake.add_fixture(url, os.path.join("sources", eid, "ok.html"))
    leads = ad.search(Q, http)
    assert [l.url for l in leads] == CASES[eid][1]
    assert leads[0].kind == "pdf" and leads[1].kind == "page"
    assert leads[0].title == "STM32F103C8 数据手册"
    assert "Cortex-M3 & 64KB" in leads[0].snippet and "<" not in leads[0].snippet
    assert [e.key for e in events] == ["engine.query", "engine.found"]


@pytest.mark.parametrize("eid", sorted(CASES))
def test_empty(tmp_path, eid):
    ad, http, fake, events, url, _ = make(tmp_path, eid)
    fake.add_fixture(url, os.path.join("sources", eid, "empty.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.empty"


@pytest.mark.parametrize("eid", sorted(CASES))
def test_captcha_rest(tmp_path, eid):
    ad, http, fake, events, url, clock = make(tmp_path, eid)
    fake.add_fixture(url, os.path.join("sources", eid, "captcha.html"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
    clock.t += 60
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
    clock.t += REST_MINUTES * 60
    fake.add_fixture(url, os.path.join("sources", eid, "ok.html"))
    assert len(ad.search(Q, http)) == 2


def test_gb18030_page(tmp_path):
    ad, http, fake, events, url, _ = make(tmp_path, "baidu")
    page = open(os.path.join(APP, "tests", "fixtures", "sources", "baidu", "ok.html"), encoding="utf-8").read()
    fake.add(url, body=page.replace("utf-8", "gb18030").encode("gb18030"))
    leads = ad.search(Q, http)
    assert leads[0].title == "STM32F103C8 数据手册"
