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
                "url": "https://www.bing.com/search?q={q}&setlang=zh-Hans", "decoder": "bing", "domains": ["bing.com"]},
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
    assert ids["bing"].available
    # выезд 2: cn.bing.com переадресует на главную www.bing.com без выдачи; с mkt=zh-CN www.bing.com отвечает
    # «нет результатов», без mkt повторяет обычный Bing — источник выключен, поисковики Китая его не зовут
    assert "bing_cn" not in ids and reg.engine("bing_cn") is None
    cn = [e for e in reg.entries(include_disabled=True) if e.id == "bing_cn"][0]
    assert not cn.enabled and cn.domains == ["bing.com"] and "cn.bing.com" not in cn.options["url"]


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


def test_results_page_mentioning_turnstile_is_not_captcha():
    """Выезд 2: обычная выдача Bing упоминает turnstile в скрипте — адаптер принимал её за капчу."""
    from chipfinder.acquire.sources.engine_html import bing_is_captcha, decode_bing
    page = ('<script>var k=["rd_tb_cnt","cf-turnstile-wrapper","rcp-"];</script><ol id="b_results">'
            '<li class="b_algo"><h2><a href="https://example.org/ne555.pdf">NE555 datasheet</a></h2><p>Timer</p></li></ol>')
    assert not bing_is_captcha(page) and decode_bing(page)[0][0] == "https://example.org/ne555.pdf"
    assert bing_is_captcha('<div class="cf-turnstile"></div><form id="b_captcha"></form>')
    # «нет результатов» с тем же скриптом — тоже не капча (так www.bing.com отвечает на mkt=zh-CN)
    empty = '<script>var k=["cf-turnstile-wrapper"];</script><ol id="b_results"><li class="b_no"><h1>没有结果</h1></li></ol>'
    assert not bing_is_captcha(empty) and decode_bing(empty) == []
    assert bing_is_captcha('<script>var k=["cf-turnstile-wrapper"];</script><div id="cf-wrapper"></div>')


def test_offtopic_results_are_dropped(tmp_path):
    """Выезд 2: Bing из сети работы отдал 10 посторонних ссылок на «NE555 datasheet pdf», адаптер сообщил «найдено 10»."""
    ad, http, fake, events, url = make(tmp_path)
    fake.add_fixture(url, os.path.join(FIX, "offtopic.html"))
    assert ad.search(Q, http) == []
    assert [e.key for e in events] == ["engine.query", "engine.offtopic"]
    assert events[-1].params["n"] == 3 and events[-1].outcome == "fail"


def test_offtopic_results_mixed_with_real(tmp_path):
    ad, http, fake, events, url = make(tmp_path)
    page = ('<ol id="b_results">'
            '<li class="b_algo"><h2><a href="https://example.org/login">Личный кабинет</a></h2><p>Забыли пароль?</p></li>'
            '<li class="b_algo"><h2><a href="https://example.org/doc/1">Datasheet</a></h2><p>STM32F103x8 STM32F103xB</p></li>'
            '<li class="b_algo"><h2><a href="https://example.org/files/stm32f103c8.pdf">PDF</a></h2><p>Download</p></li>'
            '<li class="b_algo"><h2><a href="https://example.org/a">STM32-F103 C8 board</a></h2><p>Blue pill</p></li></ol>')
    fake.add(url, body=page.encode("utf-8"))
    leads = ad.search(Q, http)
    assert [l.url for l in leads] == ["https://example.org/doc/1", "https://example.org/files/stm32f103c8.pdf",
                                      "https://example.org/a"]
    assert events[-1].key == "engine.found" and events[-1].params["n"] == 3
