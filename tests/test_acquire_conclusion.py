# -*- coding: utf-8 -*-
"""Заключение при неудаче (шаг 6.4, ARCHITECTURE §4.11): смесь капчи, блокировок и пустых выдач на fake_http."""
import logging

import pytest

from chipfinder.acquire.conclusion import build_conclusion, search_link
from chipfinder.acquire.events import EventBus
from chipfinder.acquire.models import Lead, PhotoContext
from chipfinder.acquire.orchestrator import Orchestrator
from chipfinder.acquire.registry import Registry
from chipfinder.acquire.sources.base import SourceAdapter, SourceError
from chipfinder.acquire.sources.engine_html import EngineHtml
from chipfinder.acquire.store import AcquireStore
from chipfinder.acquire.verify import SourceTrust
from chipfinder.core.interfaces import Context
from chipfinder.core.netsafe import SafeHttp
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB
from tests.fakes.fake_http import FakeHttp, FakeResponse
from tests.fixtures import make_pdfs

CAPTCHA = FakeResponse(403, b"<title>Just a moment...</title>", {"Server": "cloudflare", "CF-RAY": "1"})
PROXY = FakeResponse(403, b"<h1>ERR_ACCESS_DENIED</h1> squid/4.10", {"Server": "squid/4.10", "X-Squid-Error": "1"})
ANTIBOT = FakeResponse(403, b"<h1>Access denied</h1>", {"Server": "cloudflare", "CF-RAY": "1"})
PAGE = "https://www.alldatasheet.com/datasheet-pdf/pdf/1/TI/NE555.html"
OUTSIDE = "https://files.example.org/ne555.pdf"


class Source(SourceAdapter):
    """Источник теста: `open` — открыть страницу поиска; `captcha` — поисковик с капчей; `urls` — ссылки."""
    adapter = "fake"
    family = property(lambda self: self.entry.options.get("family", "site"))

    def find(self, query, http):
        opts = self.entry.options
        if opts.get("captcha"):
            raise SourceError("engine.captcha", "captcha", minutes=30)
        if opts.get("open"):
            http.get_html(opts["open"])
        return [Lead(url=u, kind=opts.get("kind", "pdf"), title="NE555 datasheet") for u in opts.get("urls", [])]


def source(sid, level, domain, *urls, **options):
    return dict(id=sid, adapter=options.pop("adapter", "fake"), level=level, domains=[domain], urls=list(urls),
                **options)


@pytest.fixture()
def env(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    with open(make_pdfs.write("datasheet", tmp_path), "rb") as f:
        pdf = f.read()
    return tmp_path, AcquireStore(db), pdf


def run(env, fake, sources, part="NE555P", **kw):
    tmp_path, store, _ = env
    bus = EventBus()
    allowed = [d for s in sources for d in s["domains"]]
    http = SafeHttp({"allowed_domains": allowed, "min_interval_sec": 0}, str(tmp_path / "q"),
                    logging.getLogger("t"), transport=fake)
    levels = list(dict.fromkeys(s["level"] for s in sources))
    registry = Registry({"levels": [{"id": lv} for lv in levels], "sources": sources}, bus=bus,
                        adapters={"fake": Source, "engine_html": EngineHtml})
    orch = Orchestrator(registry, http, store, bus=bus, sleep=lambda s: None,
                        trust=SourceTrust(makers=("ti.com",), catalogs=("alldatasheet.com",)), **kw)
    res = orch.search(PhotoContext(part=part, manufacturer="Texas Instruments", package="DIP-8"))
    return res, bus


def mixed(env):
    """Каталог нашёл страницу, но она под Cloudflare; магазин закрыт прокси; поисковик с капчей; пустая выдача;
    ссылка вне белого списка."""
    fake = FakeHttp()
    fake.routes[PAGE] = ANTIBOT
    fake.routes["https://shop.example/find"] = PROXY
    fake.add("https://empty.example/find", "<html>ничего</html>")
    sources = [
        source("cat", "catalog", "alldatasheet.com", PAGE, kind="page",
               url="https://www.alldatasheet.com/view.jsp?Searchword={part}"),
        source("shop", "catalog", "shop.example", open="https://shop.example/find",
               url="https://shop.example/s?k={part}"),
        source("eng", "search", "engine.example", captcha=True, family="engine", name="Поиск",
               url="https://engine.example/?q={q}"),
        source("empty", "search", "empty.example", open="https://empty.example/find"),
        source("far", "search", "far.example", OUTSIDE),
    ]
    return run(env, fake, sources)


def test_mixed_failures_are_sorted_into_sections(env):
    res, bus = mixed(env)
    c = res.conclusion
    assert res.status == "not_found" and c is not None and not c.found
    assert (c.sources, c.languages) == (5, 3)
    assert [(n.site, n.reason, n.link) for n in c.protected] == [
        ("alldatasheet.com", "antibot", "page"), ("engine.example", "captcha", "search")]
    assert c.protected[0].url == PAGE                                      # найденная страница
    assert c.protected[1].url == "https://engine.example/?q=NE555P+datasheet"   # поиск партномера на сайте
    assert [(n.site, n.reason) for n in c.blocked] == [("shop.example", "proxy")]
    assert c.blocked[0].url == "https://shop.example/s?k=NE555"
    assert [(n.site, n.url) for n in c.not_whitelisted] == [("files.example.org", OUTSIDE)]
    assert c.unclear == []
    assert "empty.example" not in c.text()                                 # пустая выдача — не неудача доступа


def test_text_is_russian_with_links_and_hint(env):
    text = mixed(env)[0].conclusion.text()
    lines = text.splitlines()
    assert lines[0].startswith("Не найдено автоматически. Проверено 5 источников на 3 языках за ")
    assert "Можно скачать вручную (сайт защищён от программ):" in lines
    assert "  alldatasheet.com — защита от программ: %s" % PAGE in lines
    assert "  engine.example — капча: https://engine.example/?q=NE555P+datasheet" in lines
    assert "Нет доступа из сети производства:" in lines
    assert "  shop.example — закрыто прокси-сервером" in lines
    assert "Не проверено: вне белого списка программы:" in lines
    assert "  files.example.org: %s" % OUTSIDE in lines
    assert lines[-1] == "Скачали вручную? Нажмите «Проверить свой PDF»."


def test_nothing_failed_gives_short_conclusion(env):
    fake = FakeHttp().add("https://empty.example/find", "<html></html>")
    res, _ = run(env, fake, [source("empty", "one", "empty.example", open="https://empty.example/find")])
    c = res.conclusion
    assert c.protected == c.blocked == c.not_whitelisted == c.unclear == []
    assert c.text().splitlines() == ["Не найдено автоматически. Проверено 1 источник на 3 языках за 0 с.",
                                     "Скачали вручную? Нажмите «Проверить свой PDF»."]


def test_one_site_is_listed_once_and_transient_is_left_out(env):
    fake = FakeHttp()
    for n in "ab":
        fake.routes["https://www.alldatasheet.com/%s.pdf" % n] = CAPTCHA
    fake.routes["https://flaky.example/find"] = FakeResponse(503, b"busy")
    fake.add_error("https://reset.example/find", ConnectionResetError("сброс"))     # первый раз — разовый сбой
    res, bus = run(env, fake, [
        source("cat", "one", "alldatasheet.com", "https://www.alldatasheet.com/a.pdf",
               "https://www.alldatasheet.com/b.pdf"),
        source("flaky", "one", "flaky.example", open="https://flaky.example/find"),
        source("reset", "one", "reset.example", open="https://reset.example/find")])
    c = res.conclusion
    assert [n.site for n in c.protected] == ["alldatasheet.com"]
    assert c.protected[0].url == "https://www.alldatasheet.com/a.pdf"
    assert c.blocked == [] and c.unclear == []
    assert sum(1 for f in res.failures if f["cls"] == "transient") == 2


def test_found_but_not_confirmed_shows_where_else_to_look(env):
    fake = FakeHttp().add("https://www.alldatasheet.com/a.pdf", env[2], content_type="application/pdf")
    fake.routes["https://shop.example/find"] = PROXY
    res, _ = run(env, fake, [source("cat", "one", "alldatasheet.com", "https://www.alldatasheet.com/a.pdf"),
                             source("shop", "two", "shop.example", open="https://shop.example/find")])
    assert res.status == "probable"
    c = res.conclusion
    assert c.found and [n.site for n in c.blocked] == ["shop.example"]
    lines = c.text().splitlines()
    assert lines[0].startswith("Найден вероятный документ, подтверждения нет.")
    assert "Где ещё посмотреть — нет доступа из сети производства:" in lines


def test_confirmed_has_no_conclusion(env):
    ti = "https://www.ti.com/lit/ds/ne555.pdf"
    fake = FakeHttp().add(ti, env[2], content_type="application/pdf")
    res, _ = run(env, fake, [source("maker", "maker", "ti.com", ti)])
    assert res.status == "confirmed" and res.conclusion is None


def test_real_engine_closed_by_proxy_goes_to_admin_list(env):
    """Поисковик, закрытый прокси, раньше давал только `engine.error` — теперь неудача классифицируется."""
    fake = FakeHttp()
    fake.routes["https://html.duckduckgo.com/html/?q=NE555+datasheet+pdf"] = PROXY
    engine = source("ddg", "search", "duckduckgo.com", adapter="engine_html", name="DuckDuckGo", lang="en",
                    decoder="ddg", url="https://html.duckduckgo.com/html/?q={q}")
    res, bus = run(env, fake, [engine], langs=["en"])
    got = [e.key for e in bus.history()]
    assert "engine.error" in got and "access.network_blocked" in got
    c = res.conclusion
    assert [(n.site, n.reason) for n in c.blocked] == [("duckduckgo.com", "proxy")] and c.languages == 1


def test_real_engine_connection_error_is_classified(env):
    fake = FakeHttp()
    fake.add_error("https://html.duckduckgo.com/html/?q=NE555+datasheet+pdf",
                   OSError("Tunnel connection failed: 407 Proxy Authentication Required"))
    engine = source("ddg", "search", "duckduckgo.com", adapter="engine_html", name="DuckDuckGo", lang="en",
                    decoder="ddg", url="https://html.duckduckgo.com/html/?q={q}")
    res, _ = run(env, fake, [engine], langs=["en"])
    assert [(n.site, n.reason) for n in res.conclusion.blocked] == [("duckduckgo.com", "proxy_auth")]


def test_search_link():
    data = {"levels": [{"id": "a"}], "sources": [
        dict(id="cat", adapter="fake", level="a", domains=["alldatasheet.com"],
             url="https://www.alldatasheet.com/view.jsp?Searchword={part}"),
        dict(id="g", adapter="fake", level="a", domains=["googleapis.com"], url="https://x/?key={key}&q={q}"),
        dict(id="e", adapter="fake", level="a", domains=["engine.example"], url="https://engine.example/?q={q}"),
        dict(id="m", adapter="fake", level="a", domains=["ti.com"])]}
    reg = Registry(data, adapters={"fake": Source})
    assert search_link(reg, "www.alldatasheet.com", "NE555P") == \
        "https://www.alldatasheet.com/view.jsp?Searchword=NE555"
    assert search_link(reg, "engine.example", "LM358/N & K") == "https://engine.example/?q=LM358%2FN+%26+K+datasheet"
    assert search_link(reg, "googleapis.com", "NE555") == ""       # шаблон с ключом человеку не годится
    assert search_link(reg, "ti.com", "NE555") == "" and search_link(reg, "other.example", "NE555") == ""


def test_build_conclusion_unknown_class_is_unclear():
    reg = Registry({"levels": [], "sources": []}, adapters={})
    fails = [{"site": "www.x.example", "cls": "unknown", "reason": "mixed", "url": "https://www.x.example/p",
              "source": "s", "level": "a"}]
    c = build_conclusion("NE555", "not_found", fails, reg, sources=2, languages=1, seconds=3.4)
    assert [(n.site, n.reason, n.url) for n in c.unclear] == [("x.example", "mixed", "https://www.x.example/p")]
    assert "Причина не ясна:" in c.text() and "за 3 с." in c.text()
