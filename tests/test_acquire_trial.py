# -*- coding: utf-8 -*-
"""Пробное получение документов (шаг 3.3a): источники → обход → скачивание → проверка PDF."""
import io
import logging
import os

from pypdf import PdfWriter

from digger.acquire import trial
from digger.acquire.models import Lead
from digger.acquire.sources.base import SourceAdapter, SourceError
from digger.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp


def pdf_bytes(pages=1):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


PDF = pdf_bytes()
LEADS = {}          # id источника → список Lead или исключение
CALLS = []


class Fake(SourceAdapter):
    adapter = "fake"
    family = "site"

    def find(self, query, http):
        CALLS.append((self.id, query.text))
        got = LEADS.get(self.id, [])
        if isinstance(got, Exception):
            raise got
        return [Lead(url=x.url, title=x.title, kind=x.kind, snippet=x.snippet) for x in got]


class FakeEngine(Fake):
    adapter = "fake_engine"
    family = "engine"


def data(*ids, **kw):
    return {"levels": [{"id": "catalog"}, {"id": "search"}, {"id": "forum"}],
            "sources": [dict({"id": i, "adapter": "fake", "level": "catalog", "domains": [i + ".com"]}, **kw) for i in ids],
            "maker_sites": {"TI": "ti.com"}}


def run(tmp_path, fake, src, parts=("NE555",), **kw):
    del CALLS[:]
    http = SafeHttp({"min_interval_sec": 0, "allowed_domains": ["ti.com", "a.com", "b.com", "c.com"]},
                    str(tmp_path / "q"), logging.getLogger("t"), transport=fake)
    kw.setdefault("sleep", lambda s: None)
    return trial.trial_downloads(None, http, data=src, parts=parts, adapters={"fake": Fake, "fake_engine": FakeEngine},
                                 **kw)


def test_direct_pdf_is_downloaded_and_validated(tmp_path):
    url = "https://www.ti.com/lit/ds/ne555.pdf"
    LEADS.clear()
    LEADS["a"] = [Lead(url=url, kind="pdf")]
    row = run(tmp_path, FakeHttp().add(url, PDF, content_type="application/pdf"), data("a"))[0]
    assert row["part"] == "NE555" and row["status"] == "ok" and row["valid"] == 1
    assert row["sources"] == [{"id": "a", "status": "ok", "leads": 1, "pdfs": 1}]
    a = row["attempts"][0]
    assert (a["step"], a["host"], a["ok"], a["valid"], a["pages"], a["size"]) == ("fetch", "www.ti.com", True, True, 1,
                                                                                 len(PDF))
    assert os.listdir(str(tmp_path / "q")) == []          # файл из карантина убран: в отчёт PDF не попадают
    assert any("скачан" in line for line in row["log"])


def test_page_is_crawled(tmp_path):
    page = "https://a.com/part/ne555"
    LEADS.clear()
    LEADS["a"] = [Lead(url=page)]
    fake = FakeHttp().add(page, '<a href="/files/ne555.pdf">Datasheet</a>').add("https://a.com/files/ne555.pdf", PDF)
    row = run(tmp_path, fake, data("a"))[0]
    assert row["status"] == "ok"
    assert [(a["step"], a["ok"]) for a in row["attempts"]] == [("crawl", True), ("fetch", True)]
    assert row["attempts"][0]["found"] == 1 and row["attempts"][1]["from_page"] == page


def test_html_instead_of_pdf_falls_back_to_crawl(tmp_path):
    url = "https://a.com/pdf/ne555.pdf"
    LEADS.clear()
    LEADS["a"] = [Lead(url=url, kind="pdf")]
    fake = FakeHttp().add(url, '<iframe src="/real/ne555.pdf"></iframe>').add("https://a.com/real/ne555.pdf", PDF)
    row = run(tmp_path, fake, data("a"))[0]
    assert [(a["step"], a["ok"]) for a in row["attempts"]] == [("fetch", False), ("crawl", True), ("fetch", True)]
    assert row["status"] == "ok"


def test_statuses_and_saved_pages(tmp_path):
    LEADS.clear()
    assert run(tmp_path, FakeHttp(), data("a"))[0]["status"] == "no_leads"

    page = "https://a.com/part/ne555"
    LEADS["a"] = [Lead(url=page), Lead(url="https://b.com/x.pdf", kind="pdf"),
                  Lead(url="https://evil.example/ne555.pdf", kind="pdf")]
    fake = FakeHttp().add(page, "<p>войдите, чтобы скачать</p>").add("https://b.com/x.pdf", "x", status=403)
    rec = tmp_path / "raw"
    row = run(tmp_path, fake, data("a"), record_dir=str(rec))[0]
    assert row["status"] == "no_pdf" and row["not_whitelisted"] == ["evil.example"]
    by = {a["host"]: a for a in row["attempts"]}
    assert by["b.com"]["failure_class"] == "site_protected" and by["b.com"]["error"] == "HTTP 403"
    assert by["a.com"]["found"] == 0 and by["a.com"]["saved"]
    assert "войдите" in (rec / by["a.com"]["saved"][0]).read_text(encoding="utf-8")

    LEADS["a"] = [Lead(url="https://b.com/x.pdf", kind="pdf")]
    fake = FakeHttp().add("https://b.com/x.pdf", b"%PDF-1.4 broken")
    row = run(tmp_path, fake, data("a"))[0]
    assert row["status"] == "invalid" and row["attempts"][0]["reason"] == "damaged"


def test_one_download_per_host_and_stop_after_two_good(tmp_path):
    LEADS.clear()
    LEADS["a"] = [Lead(url="https://a.com/%d.pdf" % i, kind="pdf") for i in range(3)] + [
        Lead(url="https://b.com/1.pdf", kind="pdf"), Lead(url="https://c.com/1.pdf", kind="pdf")]
    fake = FakeHttp()
    for n, lead in enumerate(LEADS["a"]):
        fake.add(lead.url, pdf_bytes(n + 1))
    row = run(tmp_path, fake, data("a"))[0]
    assert [a["host"] for a in row["attempts"]] == ["a.com", "b.com"] and row["valid"] == 2


def test_same_file_and_same_address_are_not_counted_twice(tmp_path):
    LEADS.clear()
    LEADS["a"] = [Lead(url="https://a.com/x.pdf", kind="pdf"), Lead(url="https://a.com/part"),
                  Lead(url="https://b.com/part"), Lead(url="https://c.com/copy.pdf", kind="pdf")]
    fake = (FakeHttp().add("https://a.com/x.pdf", PDF).add("https://c.com/copy.pdf", PDF)
            .add("https://b.com/part", '<a href="https://a.com/x.pdf">pdf</a>'))
    row = run(tmp_path, fake, data("a"))[0]
    # страница a.com не обходится (с домена уже есть годный PDF); ссылка с b.com на тот же адрес не качается снова
    assert [(a["step"], a["host"]) for a in row["attempts"]] == [("fetch", "a.com"), ("fetch", "c.com"),
                                                                 ("crawl", "b.com")]
    assert row["valid"] == 1 and row["status"] == "ok"


def test_dead_source_is_skipped_for_next_chips(tmp_path):
    LEADS.clear()
    LEADS["a"] = SourceError("engine.captcha", "Cloudflare", minutes=15)
    LEADS["b"] = [Lead(url="https://b.com/x.pdf", kind="pdf")]
    rows = run(tmp_path, FakeHttp().add("https://b.com/x.pdf", PDF), data("a", "b"), parts=("NE555", "LM358", "CH340G"))
    assert [r["sources"][0]["status"] for r in rows] == ["captcha", "captcha", "skipped"]
    assert [c for c in CALLS if c[0] == "a"] == [("a", "NE555"), ("a", "LM358")]
    assert all(r["status"] == "ok" for r in rows)


def test_adapter_crash_engine_query_and_levels(tmp_path):
    LEADS.clear()
    LEADS["a"] = ValueError("вёрстка")
    src = data("a")
    src["sources"] += [{"id": "e", "adapter": "fake_engine", "level": "search", "name": "E"},
                       {"id": "f", "adapter": "fake", "level": "forum"}]
    row = run(tmp_path, FakeHttp(), src)[0]
    assert [s["status"] for s in row["sources"]] == ["parse_error", "empty"]      # форумы в пробе не участвуют
    assert CALLS == [("a", "NE555"), ("e", "NE555 datasheet pdf")]


def test_cancel_budget_and_summary(tmp_path):
    LEADS.clear()

    class Stop:
        cancelled = True
    assert run(tmp_path, FakeHttp(), data("a"), cancel=Stop()) == []

    LEADS["a"] = [Lead(url="https://b.com/x.pdf", kind="pdf")]
    ticks = iter(range(0, 100000, 100))
    row = run(tmp_path, FakeHttp().add("https://b.com/x.pdf", PDF), data("a"), clock=lambda: next(ticks),
              chip_sec=50)[0]
    assert row["status"] in ("no_leads", "no_pdf") and row["budget"]

    rows = [{"status": "ok", "attempts": [
        {"step": "fetch", "host": "a.com", "ok": True, "valid": True, "failure_class": ""},
        {"step": "fetch", "host": "b.com", "ok": False, "valid": False, "failure_class": "site_protected"},
        {"step": "crawl", "host": "b.com", "ok": False}]}, {"status": "no_pdf", "attempts": []}]
    assert trial.summary(rows) == {"ok": 1, "no_pdf": 1}
    assert trial.host_table(rows) == [
        {"host": "a.com", "tried": 1, "downloaded": 1, "valid": 1, "failures": {}},
        {"host": "b.com", "tried": 1, "downloaded": 0, "valid": 0, "failures": {"site_protected": 1}}]
