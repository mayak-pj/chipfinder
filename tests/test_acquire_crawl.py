# -*- coding: utf-8 -*-
"""Обход страниц (шаг 3.3): a/iframe/embed/object → PDF, глубина ≤ 2, только белый список."""
import logging

from chipfinder.acquire.crawl import crawl, page_links
from chipfinder.acquire.events import EventBus
from chipfinder.acquire.models import Lead
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

PAGE = "https://www.chipdip.ru/product/ne555"
PDF = b"%PDF-1.4\n%%EOF\n"


def make(tmp_path, fake, **net):
    cfg = {"allowed_domains": ["chipdip.ru", "ti.com"], "min_interval_sec": 0}
    cfg.update(net)
    return SafeHttp(cfg, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)


def run(tmp_path, fake, lead=None, **kw):
    bus = EventBus()
    leads = crawl(make(tmp_path, fake), lead or Lead(url=PAGE, source_id="chipdip", level="market", language="ru",
                                                    query="NE555"), bus=bus, **kw)
    return leads, bus.history()


# ---------- разбор одной страницы ----------

def test_page_links_all_tags():
    html = """<html><body>
      <a href="/lit/ne555.pdf">NE555 <b>datasheet</b></a>
      <a href=/lit/unquoted.PDF>без кавычек</a>
      <iframe src="/viewer/frame.html"></iframe>
      <embed src="doc/e.pdf" type="application/pdf">
      <object data="/get?id=7" type="application/pdf"></object>
      <a href="/about">О компании</a>
      <a href="javascript:void(0)">x</a><a href="mailto:a@b.c">y</a><a href="#top">z</a>
    </body></html>"""
    pdfs, pages = page_links(html, "https://ti.com/product/ne555")
    assert [u for u, _ in pdfs] == ["https://ti.com/lit/ne555.pdf", "https://ti.com/lit/unquoted.PDF",
                                    "https://ti.com/product/doc/e.pdf", "https://ti.com/get?id=7"]
    assert pdfs[0][1] == "NE555 datasheet"
    assert pages == ["https://ti.com/viewer/frame.html"]       # /about без признаков документа не открываем


def test_page_links_base_href_viewer_and_hints():
    html = """<head><base href="https://ti.com/docs/"></head>
      <a href="viewer.html?file=%2Flit%2Fa.pdf">open</a>
      <a href="download.php?id=5">Скачать документацию</a>
      <a href="/item/5">数据手册</a>
      <a href="lit/a.pdf#page=3">again</a>
      <a href="/datasheet/ne555">more</a>"""
    pdfs, pages = page_links(html, "https://ti.com/x/y")
    assert [u for u, _ in pdfs] == ["https://ti.com/lit/a.pdf", "https://ti.com/docs/lit/a.pdf"]
    assert pages == ["https://ti.com/docs/download.php?id=5", "https://ti.com/item/5", "https://ti.com/datasheet/ne555"]


def test_page_links_broken_html():
    pdfs, pages = page_links("<a href='a.pdf'>x<iframe src=\"b.html\"><div", "https://ti.com/")
    assert [u for u, _ in pdfs] == ["https://ti.com/a.pdf"] and pages == ["https://ti.com/b.html"]


# ---------- обход ----------

def test_pdf_on_first_page(tmp_path):
    fake = FakeHttp().add(PAGE, '<title>NE555 таймер</title><a href="/pdf/ne555.pdf">Документация</a>')
    leads, events = run(tmp_path, fake)
    assert len(leads) == 1
    lead = leads[0]
    assert lead.url == "https://www.chipdip.ru/pdf/ne555.pdf" and lead.kind == "pdf"
    assert lead.title == "Документация" and lead.snippet == PAGE       # страница-источник — для Referer
    assert (lead.source_id, lead.level, lead.language, lead.query) == ("chipdip", "market", "ru", "NE555")
    assert [(e.key, e.params["site"], e.params["n"]) for e in events] == [("crawl.found", "www.chipdip.ru", 1)]
    assert events[0].lang == "ru" and events[0].source == "chipdip"


def test_depth_two_through_iframe(tmp_path):
    frame = "https://www.chipdip.ru/viewer/1"
    deep = "https://www.chipdip.ru/viewer/2"
    fake = (FakeHttp().add(PAGE, '<iframe src="/viewer/1"></iframe>')
            .add(frame, '<embed src="/pdf/a.pdf" type="application/pdf"><iframe src="/viewer/2"></iframe>')
            .add(deep, '<a href="/pdf/too_deep.pdf">x</a>'))
    leads, events = run(tmp_path, fake)
    assert [x.url for x in leads] == ["https://www.chipdip.ru/pdf/a.pdf"]
    assert leads[0].snippet == frame
    assert [u for _, u in fake.calls] == [PAGE, frame]         # третий уровень не открывается
    assert [e.key for e in events] == ["crawl.found"]


def test_depth_one(tmp_path):
    fake = FakeHttp().add(PAGE, '<iframe src="/viewer/1"></iframe>').add("https://www.chipdip.ru/viewer/1",
                                                                       '<a href="/a.pdf">x</a>')
    leads, events = run(tmp_path, fake, max_depth=1)
    assert leads == [] and len(fake.calls) == 1 and [e.key for e in events] == ["crawl.empty"]


def test_foreign_page_not_opened_foreign_pdf_kept(tmp_path):
    fake = (FakeHttp().add(PAGE, '<iframe src="https://evil.example/frame"></iframe>'
                                 '<a href="https://files.example/ne555.pdf">pdf</a>'
                                 '<a href="https://www.ti.com/lit/ne555.pdf">pdf</a>')
            .add("https://evil.example/frame", '<a href="/x.pdf">x</a>'))
    leads, _ = run(tmp_path, fake)
    assert [u for _, u in fake.calls] == [PAGE]                # чужая страница не открыта
    # ссылка на PDF вне белого списка остаётся (скачивание даст not_whitelisted → в заключение), но после своих
    assert [x.url for x in leads] == ["https://www.ti.com/lit/ne555.pdf", "https://files.example/ne555.pdf"]


def test_redirect_to_foreign_domain(tmp_path):
    fake = FakeHttp().add_redirect(PAGE, "https://evil.example/p").add("https://evil.example/p", '<a href="a.pdf">x</a>')
    leads, events = run(tmp_path, fake)
    assert leads == [] and len(fake.calls) == 1
    assert [e.key for e in events] == ["access.not_whitelisted"] and events[0].params["site"] == "evil.example"


def test_start_page_outside_whitelist(tmp_path):
    fake = FakeHttp().add("https://evil.example/p", '<a href="a.pdf">x</a>')
    leads, events = run(tmp_path, fake, lead=Lead(url="https://evil.example/p"))
    assert leads == [] and fake.calls == [] and [e.key for e in events] == ["access.not_whitelisted"]


def test_pdf_lead_returned_as_is(tmp_path):
    fake = FakeHttp()
    lead = Lead(url="https://www.ti.com/lit/ne555.pdf", kind="pdf")
    leads, events = run(tmp_path, fake, lead=lead)
    assert leads == [lead] and fake.calls == [] and events == []


def test_page_is_actually_pdf(tmp_path):
    fake = FakeHttp().add(PAGE, PDF, content_type="application/pdf")
    leads, events = run(tmp_path, fake)
    assert [(x.url, x.kind) for x in leads] == [(PAGE, "pdf")] and [e.key for e in events] == ["crawl.found"]


def test_errors_and_limits(tmp_path):
    fake = FakeHttp().add(PAGE, "nope", status=403)
    leads, events = run(tmp_path, fake)
    assert leads == [] and [e.key for e in events] == ["access.site_protected"]

    fake = FakeHttp().add(PAGE, "oops", status=500)
    assert [e.key for e in run(tmp_path, fake)[1]] == ["access.transient"]

    fake = FakeHttp().add_error(PAGE, OSError("connection reset"))
    assert [e.key for e in run(tmp_path, fake)[1]] == ["access.transient"]

    fake = FakeHttp().add(PAGE, "<p>ничего</p>")
    assert [e.key for e in run(tmp_path, fake)[1]] == ["crawl.empty"]


def test_nested_failure_does_not_lose_found(tmp_path):
    fake = FakeHttp().add(PAGE, '<a href="/a.pdf">a</a><iframe src="/f1"></iframe><iframe src="/f2"></iframe>')
    fake.add("https://www.chipdip.ru/f2", '<a href="/a.pdf">dup</a><a href="/b.pdf">b</a>')
    leads, events = run(tmp_path, fake)                        # /f1 → 404
    assert [x.url for x in leads] == ["https://www.chipdip.ru/a.pdf", "https://www.chipdip.ru/b.pdf"]
    assert [e.key for e in events] == ["crawl.found"] and events[0].params["n"] == 2


def test_page_budget_and_no_loops(tmp_path):
    body = "".join('<iframe src="/f%d"></iframe>' % i for i in range(20)) + '<iframe src="%s"></iframe>' % PAGE
    fake = FakeHttp().add(PAGE, body)
    run(tmp_path, fake, max_pages=4)
    urls = [u for _, u in fake.calls]
    assert len(urls) == 4 and urls.count(PAGE) == 1


def test_cancel(tmp_path):
    fake = FakeHttp().add(PAGE, '<iframe src="/f1"></iframe>')
    leads, events = run(tmp_path, fake, cancelled=lambda: True)
    assert leads == [] and fake.calls == [] and events == []
