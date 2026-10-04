# -*- coding: utf-8 -*-
"""Адаптеры каталогов шага 2.9: Datasheet4U (+datasheetspdf), Datasheet Archive, FindChips."""
import logging
import os

import pytest

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.catalogs.datasheet4u import Datasheet4u, download_url, hits, pdf_from_hit
from chipfinder.acquire.sources.catalogs.partlist import DatasheetArchive, FindChips, dsa_rows, fc_details
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = Query("en", "NE555 datasheet pdf", "part")
SRC = Registry.load(os.path.join(APP, "data", "sources.json"))


def entry(sid):
    return next(s for s in SRC.entries(include_disabled=True) if s.id == sid).to_dict()


def make(cls, sid, tmp_path, domain, clock=None, **over):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed([domain])
    kwargs = {"clock": clock} if clock else {}
    return cls(SourceEntry.from_dict(dict(entry(sid), **over)), bus=bus, **kwargs), http, fake, events


def read(site, name):
    with open(os.path.join(APP, "tests", "fixtures", "sources", site, name), encoding="utf-8") as f:
        return f.read()


def test_registered_and_in_sources_json():
    assert ADAPTERS["datasheet4u"] is Datasheet4u and ADAPTERS["datasheetarchive"] is DatasheetArchive
    assert ADAPTERS["findchips"] is FindChips
    ids = [a.id for a in SRC.build("catalog")]
    assert {"datasheet4u", "datasheetarchive", "findchips"} <= set(ids) and "datasheetspdf" not in ids
    assert not {"datasheet4u", "datasheetarchive", "findchips", "datasheetspdf"} & set(SRC.missing)


# ---------- Datasheet4U ----------
S4 = "https://datasheet4u.com/share_search.php?sWord=NE555"
DET = "https://datasheet4u.com/datasheets/etcTI/NE555/1395051"
DL = "https://datasheet4u.com/download/1395051/NE555.html"
PDF = "https://datasheet4u.com/pdf/1395051/NE555.pdf"


def test_d4u_hits_rows():
    found = hits(read("datasheet4u", "search.html"), S4)
    assert found[0].url == "https://datasheet4u.com/datasheets/ST-Microelectronics/NE555/454332"
    assert (found[0].part, found[0].maker, found[0].doc_id) == ("NE555", "STMicroelectronics", "454332")
    assert found[0].title == "NE555 — STMicroelectronics — General-purpose single bipolar timer"
    assert DET in [h.url for h in found] and len({h.url for h in found}) == len(found)


def test_d4u_urls_from_hit():
    h = next(h for h in hits(read("datasheet4u", "search.html"), S4) if h.url == DET)
    assert download_url(h) == DL and pdf_from_hit(h) == PDF


def test_d4u_search_opens_download_pages_and_yields_pdf(tmp_path):
    ad, http, fake, events = make(Datasheet4u, "datasheet4u", tmp_path, "datasheet4u.com")
    fake.add_fixture(S4, "sources/datasheet4u/search.html")
    fake.add_fixture(DL, "sources/datasheet4u/download.html")
    leads = ad.search(Q, http)
    pdfs = [l.url for l in leads if l.kind == "pdf"]
    assert PDF in pdfs and DET in [l.url for l in leads if l.kind == "page"]
    assert [e.key for e in events] == ["site.search", "site.found"]
    assert all(l.source_id == "datasheet4u" and l.level == "catalog" for l in leads)


def test_d4u_download_page_missing_falls_back_to_built_pdf(tmp_path):
    ad, http, fake, events = make(Datasheet4u, "datasheet4u", tmp_path, "datasheet4u.com")
    fake.add_fixture(S4, "sources/datasheet4u/search.html")
    leads = ad.search(Q, http)                    # страницы загрузки в фейке нет → сбой не капча
    assert PDF in [l.url for l in leads if l.kind == "pdf"]


def test_d4u_follow_zero_does_not_open_downloads(tmp_path):
    ad, http, fake, events = make(Datasheet4u, "datasheet4u", tmp_path, "datasheet4u.com", follow=0)
    fake.add_fixture(S4, "sources/datasheet4u/search.html")
    leads = ad.search(Q, http)
    assert leads and all(l.kind == "page" for l in leads) and [u for _, u in fake.calls] == [S4]


def test_d4u_empty_and_errors(tmp_path):
    ad, http, fake, events = make(Datasheet4u, "datasheet4u", tmp_path, "datasheet4u.com")
    fake.add(S4, "<html><body>nothing</body></html>")
    assert ad.search(Q, http) == [] and events[-1].key == "site.empty"
    fake.add(S4, "oops", status=500)
    assert ad.search(Q, http) == [] and events[-1].key == "engine.error"
    fake.add_error(S4, OSError("reset"))
    assert ad.search(Q, http) == [] and events[-1].key == "engine.error"


def test_datasheetspdf_entry_uses_same_adapter_and_is_off():
    e = entry("datasheetspdf")
    assert e["adapter"] == "datasheet4u" and e["enabled"] is False


# ---------- Datasheet Archive ----------
SA = "https://www.datasheetarchive.com/search?q=NE555"


def test_dsa_rows():
    rows = dsa_rows(read("datasheetarchive", "search.html"))
    assert rows[0] == ("NE555PE4", "Texas Instruments", "Single Precision Timer 8-PDIP 0 to 70")
    assert len(rows) == len({r[0] for r in rows}) >= 3


def test_dsa_leads_point_to_own_search_not_tracker(tmp_path):
    ad, http, fake, events = make(DatasheetArchive, "datasheetarchive", tmp_path, "datasheetarchive.com")
    fake.add_fixture(SA, "sources/datasheetarchive/search.html")
    leads = ad.search(Q, http)
    assert leads and all(l.kind == "page" and l.url.startswith("https://www.datasheetarchive.com/?q=") for l in leads)
    assert "supplyframe" not in " ".join(l.url for l in leads)
    assert leads[0].title.startswith("NE555") and "Texas Instruments" in leads[0].title
    assert events[-1].key == "site.found" and events[-1].params["pdfs"] == 0


def test_dsa_empty(tmp_path):
    ad, http, fake, events = make(DatasheetArchive, "datasheetarchive", tmp_path, "datasheetarchive.com")
    fake.add(SA, "<html><body><h1>NE555 Search Results</h1></body></html>")
    assert ad.search(Q, http) == [] and events[-1].key == "site.empty"


# ---------- FindChips ----------
SF = "https://www.findchips.com/search/NE555"


def test_fc_details():
    found = fc_details(read("findchips", "search.html"), SF)
    assert found[0] == ("https://www.findchips.com/detail/NE555D/STMicroelectronics", "NE555D", "STMicroelectronics")
    urls = [d[0] for d in found]
    assert len(urls) == len(set(urls)) and not any("/search/" in u for u in urls)
    assert ("https://www.findchips.com/detail/NE555S-13/Diodes-Incorporated", "NE555S-13", "Diodes Incorporated") in found


def test_fc_leads(tmp_path):
    ad, http, fake, events = make(FindChips, "findchips", tmp_path, "findchips.com")
    fake.add_fixture(SF, "sources/findchips/search.html")
    leads = ad.search(Q, http)
    assert leads and all(l.kind == "page" for l in leads) and leads[0].title == "NE555D — STMicroelectronics"
    assert [u for _, u in fake.calls] == [SF]                       # детали (Cloudflare) не открываем
    assert events[-1].key == "site.found"


def test_fc_empty(tmp_path):
    ad, http, fake, events = make(FindChips, "findchips", tmp_path, "findchips.com")
    fake.add_fixture(SF, "sources/findchips/empty.html")
    assert ad.search(Q, http) == [] and events[-1].key == "site.empty"


@pytest.mark.parametrize("cls,sid,domain,url,fix", [
    (FindChips, "findchips", "findchips.com", SF, "findchips"),
    (DatasheetArchive, "datasheetarchive", "datasheetarchive.com", SA, "findchips"),
    (Datasheet4u, "datasheet4u", "datasheet4u.com", S4, "findchips"),
])
@pytest.mark.parametrize("status", [200, 403])
def test_cloudflare_is_captcha_and_rests(tmp_path, cls, sid, domain, url, fix, status):
    now = [1000.0]
    ad, http, fake, events = make(cls, sid, tmp_path, domain, clock=lambda: now[0])
    fake.add_fixture(url, "sources/%s/cloudflare.html" % fix, status=status)
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha" and events[-1].params["minutes"] == 15
    calls = len(fake.calls)
    now[0] += 60
    assert ad.search(Q, http) == [] and len(fake.calls) == calls
