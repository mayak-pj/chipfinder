# -*- coding: utf-8 -*-
"""Шаг 3.4c: каталоги по отчёту выезда 2 — свой срок ответа, защита LCSC (203), вкладки выдачи Чип и Дип,
адреса отвергнутых ссылок в пробе downloads."""
import logging
import os

from digger.acquire.models import Lead
from digger.acquire.registry import Registry
from digger.acquire.sources.catalogs import china
from digger.acquire.sources.catalogs.common import is_blocked
from digger.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp
from tests.test_source_china_catalogs import Q, SEARCH, make
from tests.test_acquire_trial import data, run, LEADS, PDF

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = ("<!DOCTYPE html><html><head><script>var _xvasu = 1;</script><script>"
        "function a(){document.cookie='x=1';window.location.reload();}a();</script></head></html>")


def test_lcsc_203_cookie_stub_is_protection_not_empty(tmp_path):
    assert is_blocked(203, STUB) and not is_blocked(200, "<html>" + "x" * 30000 + "</html>")
    ad, http, fake, events = make(tmp_path)
    fake.add(SEARCH, STUB.encode("utf-8"), status=203)
    assert ad.search(Q, http) == [] and events[-1].key == "engine.captcha"


def test_chipdip_service_tabs_are_not_product_cards():
    page = ('<a href="/search/video?searchtext=NE555">NE555 видео</a>'
            '<a href="/search/text?searchtext=NE555">NE555 текст</a>'
            '<a href="/product/ne555dr-64173">NE555DR</a>')
    details, _ = china.links(page, "https://www.chipdip.ru/search", ["chipdip.ru"], ["NE555"])
    assert [d[0] for d in details] == ["https://www.chipdip.ru/product/ne555dr-64173"]


def test_source_timeout_reaches_the_request(tmp_path):
    seen = []

    class Session:
        def request(self, method, url, headers=None, timeout=None, **kw):
            seen.append(timeout)
            return FakeHttp().add(url, b"<html></html>")("GET", url, headers)

    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"))
    http.add_allowed(["a.com"])
    http._session = Session()
    http.fetch("https://a.com/")
    http.fetch("https://a.com/", timeout=45)
    assert seen == [http.timeout, 45]


def test_catalog_site_passes_entry_timeout(tmp_path):
    ad, http, fake, events = make(tmp_path, timeout=45)
    got = []

    class Spy:
        def fetch(self, url, **kw):
            got.append(kw)
            return {"url": url, "status": 200, "body": b"<html></html>"}

    ad._get(Spy(), SEARCH)
    ad.entry.options.pop("timeout")
    ad._get(Spy(), SEARCH)
    assert got == [{"timeout": 45.0}, {}]


def test_sources_json_datasheetarchive_slow_findchips_off():
    entries = {e.id: e for e in Registry.load(os.path.join(APP, "data", "sources.json")).entries(include_disabled=True)}
    assert entries["datasheetarchive"].options["timeout"] >= 30 and entries["datasheetarchive"].enabled
    assert not entries["findchips"].enabled


def test_trial_upgrades_http_and_records_refused_addresses(tmp_path):
    LEADS.clear()
    good = "https://a.com/ne555.pdf"
    LEADS["a"] = [Lead(url="http://a.com/ne555.pdf", kind="pdf"),
                  Lead(url="https://evil.example/ne555.pdf", kind="pdf")]
    row = run(tmp_path, FakeHttp().add(good, PDF, content_type="application/pdf"), data("a"))[0]
    assert row["valid"] == 1 and row["attempts"][0]["url"] == good
    assert row["not_whitelisted"] == ["evil.example"]
    assert row["not_whitelisted_urls"] == [{"url": "https://evil.example/ne555.pdf", "host": "evil.example",
                                            "source": "a", "reason": "domain"}]
