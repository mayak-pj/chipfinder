# -*- coding: utf-8 -*-
"""Китайские каталоги шага 2.10: адаптер direct_url (LCSC/szlcsc, Semiee). Вёрстка синтетическая."""
import logging
import os


from chipfinder.acquire.events import EventBus
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.catalogs.china import DirectUrl, links
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = Query("zh", "W25Q64JV datasheet pdf", "part")
SRC = Registry.load(os.path.join(APP, "data", "sources.json"))
SEARCH = "https://so.szlcsc.com/global.html?k=W25Q64JV"
DETAIL = "https://item.szlcsc.com/97521.html"
PDF = "https://atta.szlcsc.com/upload/public/pdf/source/W25Q64JV.pdf"
PAGE = ('<a href="%s">W25Q64JVSSIQ 闪存</a><a href="/about.html">关于</a>'
        '<a href="https://evil.example/W25Q64JV.pdf">x</a><a href="%s">W25Q64JV.pdf</a>')


def make(tmp_path, sid="lcsc", clock=None, **over):
    entry = next(s for s in SRC.entries() if s.id == sid).to_dict()
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["szlcsc.com", "semiee.com", "evil.example"])
    kwargs = {"clock": clock} if clock else {}
    return DirectUrl(SourceEntry.from_dict(dict(entry, **over)), bus=bus, **kwargs), http, fake, events


def test_registered_and_in_sources_json():
    assert ADAPTERS["direct_url"] is DirectUrl
    assert {"lcsc", "semiee"} <= {a.id for a in SRC.build("china")}
    assert "direct_url" not in SRC.missing


def test_links_only_source_domains_and_part():
    details, pdfs = links(PAGE % (DETAIL, PDF), SEARCH, ["szlcsc.com"], ["W25Q64JV"])
    assert details == [(DETAIL, "W25Q64JVSSIQ 闪存")] and pdfs == [PDF]


def test_search_follows_detail_for_pdf(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add(SEARCH, (PAGE % (DETAIL, PDF)).encode("utf-8"))
    fake.add(DETAIL, ('<a href="%s">下载 datasheet</a>' % "/upload/W25Q64JV_rev.pdf").encode("utf-8"))
    leads = ad.search(Q, http)
    assert PDF in [l.url for l in leads if l.kind == "pdf"]
    assert "https://item.szlcsc.com/upload/W25Q64JV_rev.pdf" in [l.url for l in leads if l.kind == "pdf"]
    assert DETAIL in [l.url for l in leads if l.kind == "page"]
    assert [e.key for e in events] == ["site.search", "site.found"]
    assert all(l.source_id == "lcsc" and l.level == "china" for l in leads)


def test_empty_script_page_gives_no_leads(tmp_path):
    ad, http, fake, events = make(tmp_path, "semiee")
    fake.add("https://www.semiee.com/search?searchModel=W25Q64JV", b'<div id="pageData"></div>')
    assert ad.search(Q, http) == []
    assert events[-1].key == "site.empty"


def test_waf_block_is_captcha_and_rests(tmp_path):
    now = [1000.0]
    ad, http, fake, events = make(tmp_path, clock=lambda: now[0])
    fake.add(SEARCH, "<html><title>WAF拦截页面</title></html>".encode("utf-8"), status=403)
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha" and events[-1].params["minutes"] == 15
    n = len(fake.calls) if hasattr(fake, "calls") else None
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"
    if n is not None:
        assert len(fake.calls) == n                      # в отдых в сеть не ходим
    now[0] += 16 * 60
    fake.add(SEARCH, (PAGE % (DETAIL, PDF)).encode("utf-8"))
    assert PDF in [l.url for l in ad.search(Q, http) if l.kind == "pdf"]


def test_other_part_empty_query(tmp_path):
    ad, http, fake, events = make(tmp_path)
    assert ad.find("", http) == []
