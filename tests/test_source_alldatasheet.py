# -*- coding: utf-8 -*-
"""Адаптер AllDatasheet (шаг 2.8): выдача → страницы деталей → PDF, Cloudflare как капча, события."""
import logging
import os

import pytest

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.catalogs.alldatasheet import (
    AllDatasheet, detail_links, is_blocked, pdf_from_detail, pdf_links,
)
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join("sources", "alldatasheet")
SEARCH = "https://www.alldatasheet.com/view.jsp?Searchword=NE555"
D1 = "https://www.alldatasheet.com/datasheet-pdf/view/28756/ONSEMI/NE555.html"
D2 = "https://www.alldatasheet.com/datasheet-pdf/view/12345/TI/NE555P.html"
PDF1 = "https://pdf1.alldatasheet.com/datasheet-pdf/pdf/28756/ONSEMI/NE555.html?ref=1"
ENTRY = {"id": "alldatasheet", "adapter": "alldatasheet", "name": "AllDatasheet", "level": "catalog", "lang": "en",
         "url": "https://www.alldatasheet.com/view.jsp?Searchword={part}", "domains": ["alldatasheet.com"], "follow": 1}
Q = Query("en", "NE555 datasheet pdf", "part")


def make(tmp_path, entry=ENTRY, clock=None):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["alldatasheet.com"])
    kwargs = {"clock": clock} if clock else {}
    return AllDatasheet(SourceEntry.from_dict(entry), bus=bus, **kwargs), http, fake, events


def read(name):
    with open(os.path.join(APP, "tests", "fixtures", FIX, name), encoding="utf-8") as f:
        return f.read()


def test_registered_and_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    assert ADAPTERS["alldatasheet"] is AllDatasheet
    assert "alldatasheet" in [a.id for a in reg.build("catalog")] and "alldatasheet" not in reg.missing


def test_detail_links_absolute_deduplicated_only_details():
    links = detail_links(read("search.html"), SEARCH)
    assert [u for u, _ in links] == [D1, D2, "https://www.alldatasheet.com/datasheet-pdf/view/99999/STMICROELECTRONICS/NE556.html"]
    assert links[0][1] == "NE555"


def test_pdf_links_from_a_and_iframe():
    assert pdf_links(read("detail.html"), D1) == [PDF1, "https://www.alldatasheet.com/datasheet-pdf/pdf/28756/ONSEMI/NE555.html"]
    assert pdf_links(read("detail_nopdf.html"), D2) == []


def test_pdf_from_detail():
    assert pdf_from_detail(D2) == "https://www.alldatasheet.com/datasheet-pdf/pdf/12345/TI/NE555P.html"


def test_search_follows_details_and_yields_pdfs(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add_fixture(SEARCH, FIX + "/search.html")
    fake.add_fixture(D1, FIX + "/detail.html")
    fake.add_fixture(D2, FIX + "/detail_nopdf.html")
    fake.add("https://www.alldatasheet.com/datasheet-pdf/view/99999/STMICROELECTRONICS/NE556.html", "<html></html>")
    leads = ad.search(Q, http)
    pdfs = [l.url for l in leads if l.kind == "pdf"]
    assert PDF1 in pdfs
    assert "https://www.alldatasheet.com/datasheet-pdf/pdf/12345/TI/NE555P.html" in pdfs      # PDF построен из адреса детали
    assert all(l.source_id == "alldatasheet" and l.level == "catalog" for l in leads)
    assert D1 in [l.url for l in leads if l.kind == "page"]
    assert [e.key for e in events] == ["site.search", "site.found"]
    assert events[-1].params["pdfs"] == len(pdfs)


def test_own_part_numbers_come_first(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add_fixture(SEARCH, FIX + "/search.html")
    leads = ad.find(Q, http)
    first = [l.url for l in leads][:2]
    assert all("NE555" in u for u in first) and "NE556" not in " ".join(l.url for l in leads[:4])


def test_follow_zero_does_not_open_details(tmp_path):
    ad, http, fake, events = make(tmp_path, dict(ENTRY, follow=0))
    fake.add_fixture(SEARCH, FIX + "/search.html")
    leads = ad.search(Q, http)
    assert leads and all(l.kind == "page" for l in leads)
    assert [u for _, u in fake.calls] == [SEARCH]


def test_empty_result(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add_fixture(SEARCH, FIX + "/empty.html")
    assert ad.search(Q, http) == []
    assert events[-1].key == "site.empty"


@pytest.mark.parametrize("status", [200, 403, 503])
def test_cloudflare_is_captcha_and_domain_rests(tmp_path, status):
    now = [1000.0]
    ad, http, fake, events = make(tmp_path, clock=lambda: now[0])
    fake.add_fixture(SEARCH, FIX + "/cloudflare.html", status=status)
    assert ad.search(Q, http) == []
    assert events[-1].key == "engine.captcha" and events[-1].params["minutes"] == 15
    calls = len(fake.calls)
    now[0] += 60
    assert ad.search(Q, http) == [] and len(fake.calls) == calls          # отдых: в сеть не ходим
    assert events[-1].key == "engine.captcha"
    now[0] += 15 * 60
    fake.add_fixture(SEARCH, FIX + "/empty.html")
    ad.search(Q, http)
    assert events[-1].key == "site.empty"


def test_captcha_on_detail_page_stops(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add_fixture(SEARCH, FIX + "/search.html")
    fake.add_fixture(D1, FIX + "/cloudflare.html", status=403)
    assert ad.search(Q, http) == []
    assert events[-1].key == "engine.captcha"


def test_network_error_and_http_error(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add_error(SEARCH, OSError("reset"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.error"
    fake.add(SEARCH, "oops", status=500)
    assert ad.search(Q, http) == [] and events[-1].key == "engine.error"


def test_is_blocked_does_not_trigger_on_normal_pages():
    assert not is_blocked(200, read("search.html")) and not is_blocked(404, read("empty.html"))
    assert is_blocked(200, read("cloudflare.html"))
