# -*- coding: utf-8 -*-
"""Оркестратор поиска (шаг 6.1, ARCHITECTURE §4.6): уровни, бюджет, остановка, отмена — на fake_http."""
import logging
import os

import pytest

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.models import Lead, PhotoContext
from chipfinder.acquire.orchestrator import Budget, Orchestrator, from_context
from chipfinder.acquire.registry import Registry
from chipfinder.acquire.sources.base import SourceAdapter
from chipfinder.acquire.store import AcquireStore
from chipfinder.acquire.verify import SourceTrust
from chipfinder.core.interfaces import Context
from chipfinder.core.netsafe import SafeHttp
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB
from tests.fakes.fake_http import FakeHttp, FakeResponse
from tests.fixtures import make_pdfs

TRUST = SourceTrust(makers=("ti.com",), catalogs=("alldatasheet.com", "datasheet4u.com"))
TI = "https://www.ti.com/lit/ds/ne555.pdf"
ALL = "https://www.alldatasheet.com/%s.pdf"
D4U = "https://www.datasheet4u.com/ne555.pdf"


class FakeSource(SourceAdapter):
    """Источник из sources.json теста: отдаёт `urls` как ссылки на PDF; `open` — сначала открывает страницу."""
    adapter = "fake"

    def find(self, query, http):
        if self.entry.options.get("open"):
            http.get_html(self.entry.options["open"])
        return [Lead(url=u, kind="pdf", title="NE555 datasheet") for u in self.entry.options.get("urls", [])]


def source(sid, level, *urls, **options):
    return dict(id=sid, adapter="fake", level=level, domains=["%s.example" % sid], urls=list(urls), **options)


@pytest.fixture()
def env(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    with open(make_pdfs.write("datasheet", tmp_path), "rb") as f:
        pdf = f.read()
    return tmp_path, AcquireStore(db), pdf


def build(env, fake, sources, **kw):
    tmp_path, store, _ = env
    bus = EventBus()
    net = {"allowed_domains": ["ti.com", "alldatasheet.com", "datasheet4u.com", "a.example"], "min_interval_sec": 0}
    http = SafeHttp(net, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)
    levels = list(dict.fromkeys(s["level"] for s in sources))
    registry = Registry({"levels": [{"id": lv} for lv in levels], "sources": sources}, bus=bus,
                        adapters={"fake": FakeSource})
    orch = Orchestrator(registry, http, store, bus=bus, trust=TRUST, sleep=lambda s: None, **kw)
    return orch, bus


def search(orch, **kw):
    return orch.search(PhotoContext(part="NE555P", manufacturer="Texas Instruments", package="DIP-8"), **kw)


def keys(bus):
    return [e.key for e in bus.history()]


def pdf_at(fake, pdf, *urls):
    for url in urls:
        fake.add(url, pdf, content_type="application/pdf")
    return fake


def test_maker_on_first_level_stops_search(env):
    fake = pdf_at(FakeHttp(), env[2], TI, ALL % "a")
    orch, bus = build(env, fake, [source("maker", "maker", TI), source("cat", "catalog", ALL % "a")])
    res = search(orch)
    assert res.status == "confirmed" and res.reason == "confirmed"
    assert os.path.isfile(res.path) and "confirmed" in res.path
    assert (res.queries, res.downloads, res.sources) == (1, 1, 1)
    assert [u for _, u in fake.calls] == [TI]                    # второй уровень не трогали
    got = keys(bus)
    assert got == ["local.search", "local.empty", "site.search", "site.found", "fetch.start", "fetch.done",
                   "quarantine.placed", "validate.ok", "verify.result", "result.confirmed"]
    last = bus.history()[-1]
    assert last.source == "maker" and last.params["queries"] == 1
    assert all(e.level == "maker" for e in bus.history()[2:-1])


def test_second_search_takes_document_from_library(env):
    fake = pdf_at(FakeHttp(), env[2], TI)
    orch, bus = build(env, fake, [source("maker", "maker", TI)])
    first = search(orch)
    calls = len(fake.calls)
    res = search(orch)
    assert res.status == "confirmed" and res.reason == "local" and res.path == first.path
    assert len(fake.calls) == calls
    assert keys(bus)[-3:] == ["local.search", "local.found", "result.confirmed"]


def test_only_probable_escalates_until_budget(env):
    urls = [ALL % n for n in "abc"]
    fake = pdf_at(FakeHttp(), env[2], *urls)
    sources = [source("s%d" % i, "level%d" % i, url) for i, url in enumerate(urls)]
    orch, bus = build(env, fake, sources, budget=Budget(downloads=2))
    res = search(orch)
    assert res.status == "probable" and res.reason == "budget" and res.downloads == 2
    assert "probable" in res.path and res.best.verdict.reasons == ["unconfirmed"]
    assert [u for _, u in fake.calls] == urls[:2]                # два уровня пройдено, третий — уже нет
    got = keys(bus)
    assert got.count("confirm.none") == 2 and got[-2:] == ["search.limit", "result.probable"]


def test_time_budget(env):
    fake = pdf_at(FakeHttp(), env[2], ALL % "a", ALL % "b")
    ticks = iter(range(0, 100000, 100))
    orch, bus = build(env, fake, [source("s1", "one", ALL % "a"), source("s2", "two", ALL % "b")],
                      budget=Budget(seconds=150), clock=lambda: next(ticks))
    res = search(orch)
    assert res.reason == "budget" and keys(bus)[-2] == "search.budget"
    assert bus.history()[-2].params["seconds"] == 150


def test_same_document_on_independent_sites_is_confirmed(env):
    fake = pdf_at(FakeHttp(), env[2], ALL % "a", D4U)
    orch, bus = build(env, fake, [source("s1", "one", ALL % "a"), source("s2", "two", D4U)])
    res = search(orch)
    assert res.status == "confirmed" and res.downloads == 2
    assert sorted(res.best.sources_agreeing) == ["alldatasheet.com", "datasheet4u.com"]
    assert "confirm.identical" in keys(bus)


def test_everywhere_ignores_stop_on_result(env):
    fake = pdf_at(FakeHttp(), env[2], TI, ALL % "a")
    orch, _ = build(env, fake, [source("maker", "maker", TI), source("cat", "catalog", ALL % "a")])
    res = search(orch, everywhere=True)
    assert res.status == "confirmed" and res.reason == "exhausted" and res.downloads == 2
    assert res.best.lead.source_id == "maker"


def test_all_unreachable_gives_clear_reason(env):
    fake = FakeHttp()
    fake.add_error("https://a.example/find", ConnectionResetError("сброс"))
    fake.routes[ALL % "a"] = FakeResponse(403, b"<title>Just a moment...</title>",
                                          {"Server": "cloudflare", "CF-RAY": "1"})
    sources = [source("a", "one", open="https://a.example/find"), source("s2", "two", ALL % "a"),
               source("s3", "three", "https://files.example.org/ne555.pdf")]
    orch, bus = build(env, fake, sources)
    res = search(orch)
    assert res.status == "not_found" and res.reason == "exhausted" and res.path == "" and res.sources == 3
    by_site = {f["site"]: f for f in res.failures}
    assert by_site["a.example"]["cls"] in ("transient", "network_blocked")
    assert by_site["www.alldatasheet.com"]["cls"] == "site_protected"
    assert by_site["www.alldatasheet.com"]["url"] == ALL % "a"
    assert by_site["files.example.org"]["cls"] == "not_whitelisted"
    got = keys(bus)
    assert "access.site_protected" in got and "access.not_whitelisted" in got and got[-1] == "result.not_found"
    assert bus.history()[-1].params["sources"] == 3
    assert res.downloads == 1                                   # ссылка вне белого списка бюджет не тратит


def test_cancel_in_the_middle(env):
    fake = pdf_at(FakeHttp(), env[2], ALL % "a", ALL % "b")
    orch, bus = build(env, fake, [source("s1", "one", ALL % "a"), source("s2", "two", ALL % "b")])
    unsubscribe = bus.subscribe(lambda e: e.key == "fetch.done" and orch.cancel())
    res = search(orch)
    unsubscribe()
    assert res.status == "cancelled" and res.reason == "cancelled"
    got = keys(bus)
    assert got[-1] == "search.cancelled" and not [k for k in got if k.startswith("result.")]
    assert [u for _, u in fake.calls] == [ALL % "a"]
    assert search(orch).status != "cancelled"                    # следующий поиск отмену не наследует


def test_broken_source_does_not_break_search(env):
    class Broken(FakeSource):
        def find(self, query, http):
            raise RuntimeError("разметка сайта изменилась")

    fake = pdf_at(FakeHttp(), env[2], TI)
    orch, bus = build(env, fake, [source("bad", "one"), source("maker", "maker", TI)])
    orch.registry._built["bad"] = Broken(orch.registry.entries("one")[0], bus=bus)
    res = search(orch)
    assert res.status == "confirmed" and "error.internal" in keys(bus)


def test_from_context_runs_real_sources(ctx, tmp_path):
    """Настоящие источники из sources.json на пустой подменной сети: поиск доходит до конца без ошибок."""
    fake = FakeHttp()
    net = dict(ctx.config["network"], offline=False, min_interval_sec=0)
    http = SafeHttp(net, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)
    bus = EventBus()
    orch = from_context(ctx, bus, http=http)
    assert (orch.budget.queries, orch.budget.downloads, orch.budget.seconds) == (60, 10, 180)
    assert http.is_allowed("https://www.ti.com/lit/ds/ne555.pdf")
    orch.budget = Budget(queries=25)
    orch.sleep = lambda s: None
    res = orch.search(PhotoContext(part="NE555P", manufacturer="Texas Instruments"))
    got = keys(bus)
    assert res.status == "not_found" and res.reason == "budget" and res.queries >= 25
    assert "error.internal" not in got and got[-2:] == ["search.limit", "result.not_found"]
    assert len(set(e.lang for e in bus.history())) >= 2 and fake.calls
    assert orch.recorder.stats.search_count() == 1               # статистика поиска записана
