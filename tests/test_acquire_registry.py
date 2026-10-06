# -*- coding: utf-8 -*-
"""Интерфейс адаптера и реестр источников (шаг 1.4, ARCHITECTURE §4.2)."""
import logging
import os

import pytest

from chipfinder.acquire.events import LANGS, EventBus, render
from chipfinder.acquire.models import Lead
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import Registry, legacy_sources
from chipfinder.acquire.sources.base import SourceAdapter, SourceEntry
from chipfinder.core.config import read_json
from chipfinder.core.netsafe import SafeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(APP, "data", "sources.json")


class FakeEngine(SourceAdapter):
    adapter = "fake_engine"
    family = "engine"

    def find(self, query, http):
        self.calls = getattr(self, "calls", 0) + 1
        return [Lead(url=u) for u in self.entry.options.get("urls", [])]


class FakeSite(FakeEngine):
    adapter = "fake_site"
    family = "site"
    kinds = ("pdf", "page")


ADAPTERS = {"fake_engine": FakeEngine, "fake_site": FakeSite}

DATA = {
    "levels": [
        {"id": "catalog", "name": "Каталоги", "enabled": True},
        {"id": "search", "name": "Поисковики", "enabled": True},
        {"id": "off", "name": "Выключенный уровень", "enabled": False},
    ],
    "sources": [
        {"id": "eng", "adapter": "fake_engine", "name": "Engine", "level": "search", "lang": "en",
         "domains": ["engine.example"], "urls": ["https://a.example/x.pdf", "https://b.example/"]},
        {"id": "api", "adapter": "fake_engine", "name": "Api", "level": "search", "lang": "en",
         "domains": ["api.example"], "needs_key": "api", "urls": ["https://c.example/"]},
        {"id": "cat", "adapter": "fake_site", "name": "Каталог", "level": "catalog", "lang": "zh",
         "domains": ["cat.example.cn"], "queries": ["{part} 规格书"]},
        {"id": "disabled", "adapter": "fake_site", "level": "catalog", "domains": ["disabled.example"],
         "enabled": False},
        {"id": "in_off_level", "adapter": "fake_site", "level": "off", "domains": ["off.example"]},
        {"id": "later", "adapter": "not_written_yet", "level": "search", "domains": ["later.example"]},
    ],
    "maker_sites": {"Winbond": "winbond.com"},
    "pdf_hosts": ["pdfhost.example"],
}


def _registry(keys=None, bus=None):
    return Registry(DATA, keys=keys, bus=bus, adapters=ADAPTERS)


# ---------- реестр ----------

def test_order_follows_levels_and_disabled_are_skipped():
    reg = _registry()
    assert [e.id for e in reg.entries()] == ["cat", "eng", "api", "later"]
    assert [e.id for e in reg.entries(level="search")] == ["eng", "api", "later"]
    assert [e.id for e in reg.entries(include_disabled=True)][-1] == "in_off_level"
    assert [lv["id"] for lv in reg.levels()] == ["catalog", "search"]


def test_build_adapters_and_unknown_type_is_reported():
    reg = _registry()
    built = reg.build()
    assert [a.id for a in built] == ["cat", "eng", "api"]
    assert isinstance(built[0], FakeSite) and built[0].level == "catalog" and built[0].lang == "zh"
    assert built[0].entry.queries == ["{part} 规格书"]
    assert reg.missing == ["later"]
    assert [a.id for a in reg.build(level="catalog")] == ["cat"]


def test_domains_go_to_whitelist(tmp_path):
    reg = _registry()
    allowed = reg.allowed_domains()
    for d in ("engine.example", "cat.example.cn", "winbond.com", "pdfhost.example"):
        assert d in allowed
    assert "disabled.example" not in allowed and "off.example" not in allowed
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"))
    assert not http.is_allowed("https://www.engine.example/search?q=1")
    http.add_allowed(allowed)
    assert http.is_allowed("https://www.engine.example/search?q=1")
    assert not http.is_allowed("https://disabled.example/")


@pytest.mark.parametrize("bad", [
    {"sources": [{"adapter": "x"}]},
    {"sources": [{"id": "a"}]},
    {"sources": [{"id": "a", "adapter": "x"}, {"id": "a", "adapter": "y"}]},
    {"sources": [{"id": "a", "adapter": "x", "lang": "de"}]},
])
def test_bad_entries_are_rejected(bad):
    with pytest.raises(ValueError):
        Registry(bad)


def test_entry_roundtrip_keeps_adapter_options():
    raw = {"id": "ddg", "adapter": "engine_html", "url": "https://x/?q={q}", "decoder": "ddg", "domains": ["x"]}
    entry = SourceEntry.from_dict(raw)
    assert entry.enabled and entry.options == {"url": "https://x/?q={q}", "decoder": "ddg"}
    assert SourceEntry.from_dict(entry.to_dict()) == entry


# ---------- адаптер и события ----------

def test_engine_publishes_query_and_found():
    bus = EventBus()
    eng = _registry(bus=bus).build(level="search")[0]
    leads = eng.search(Query("ru", "W25Q64JV даташит", "part"), http=None)
    assert [l.url for l in leads] == ["https://a.example/x.pdf", "https://b.example/"]
    assert all(l.source_id == "eng" and l.level == "search" and l.query == "W25Q64JV даташит"
               and l.language == "ru" for l in leads)
    first, last = bus.history()
    assert (first.key, first.outcome, last.key, last.outcome) == ("engine.query", "", "engine.found", "found")
    assert last.lang == "ru" and last.source == "eng" and last.level == "search" and last.params["n"] == 2
    assert render(last) == "Engine: «W25Q64JV даташит» — найдено 2 ссылки"


def test_site_publishes_search_and_empty_in_site_language():
    bus = EventBus()
    site = _registry(bus=bus).build(level="catalog")[0]
    site.entry.options["urls"] = []
    assert site.search("W25Q64JV", http=None) == []
    keys = [(e.key, e.lang, e.params["site"]) for e in bus.history()]
    assert keys == [("site.search", "zh", "cat.example.cn"), ("site.empty", "zh", "cat.example.cn")]


def test_adapter_without_key_is_skipped_with_event():
    bus = EventBus()
    api = _registry(bus=bus).build(level="search")[1]
    assert api.needs_key and not api.available
    assert api.search(Query("en", "W25Q64JV datasheet pdf", "part"), http=None) == []
    assert getattr(api, "calls", 0) == 0
    (event,) = bus.history()
    assert (event.key, event.outcome, event.source) == ("engine.no_key", "skip", "api")
    assert render(event) == 'Api query: "W25Q64JV datasheet pdf" — no API key, skipped'


@pytest.mark.parametrize("keys, ok", [
    ({"api": "secret"}, True), ({"api": {"key": "k", "cx": "c"}}, True),
    ({"api": {"key": "k", "cx": ""}}, False), ({"api": ""}, False), ({}, False),
])
def test_key_presence(keys, ok):
    api = _registry(keys=keys).build(level="search")[1]
    assert api.available is ok
    assert len(api.search("W25Q64JV datasheet", http=None)) == (1 if ok else 0)


def test_base_adapter_is_abstract():
    with pytest.raises(NotImplementedError):
        SourceAdapter(SourceEntry(id="x", adapter="x")).search("q", http=None)


# ---------- data/sources.json ----------

def test_shipped_sources_are_valid():
    reg = Registry.load(SOURCES)
    entries = reg.entries(include_disabled=True)
    level_ids = [lv["id"] for lv in reg.levels(include_disabled=True)]
    assert len(entries) >= 20
    for e in entries:
        assert e.level in level_ids, e.id
        assert e.domains or e.adapter == "engine_queries", e.id
        assert e.lang in LANGS + ("",), e.id
    by_id = {e.id: e for e in entries}
    assert by_id["google"].needs_key == "google_api" and by_id["google"].adapter == "google_api"
    assert by_id["baidu"].lang == "zh" and by_id["yandex"].lang == "ru"
    for d in ("duckduckgo.com", "alldatasheet.com", "szlcsc.com", "st.com", "api.github.com"):
        assert d in reg.allowed_domains()


def test_legacy_view_for_v1_search():
    old = legacy_sources(read_json(SOURCES))
    assert set(old["engines"]) == {"duckduckgo", "bing", "mojeek", "brave", "yandex", "baidu", "bing_cn", "sogou",
                                   "so360"}
    assert [lv["id"] for lv in old["levels"]] == ["catalog", "maker", "world", "china", "russian", "forum", "marking",
                                                   "github"]
    catalog, marking, github = old["levels"][0], old["levels"][6], old["levels"][7]
    assert [d["name"] for d in catalog["direct"]] == ["AllDatasheet", "Datasheet Archive", "Datasheet4U"]
    assert catalog["engines"] == ["duckduckgo", "bing", "mojeek", "brave"] and len(catalog["queries"]) == 3
    assert marking["for_codes"] and "{code}" in marking["queries"][0]
    assert github["github_api"].startswith("https://api.github.com/")
    assert legacy_sources(old) is old


def test_china_marketplaces_via_search_engines():
    """Шаг 2.11: AliExpress, Taobao, 1688 — запросы с site: у китайских поисковиков, домены в белом списке."""
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data" / "sources.json").read_text(encoding="utf-8"))
    src = {s["id"]: s for s in data["sources"]}["china_queries"]
    text = " ".join(src["queries"])
    for site in ("aliexpress.com", "taobao.com", "1688.com"):
        assert "site:" + site in text
        assert site in data["pdf_hosts"]
    assert all("{part}" in q for q in src["queries"])


def test_russian_marketplaces():
    """Шаг 2.12: Чип и Дип — прямой адаптер; Промэлектроника, ЭФО, Ozon — запросы с site: у поисковиков."""
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data" / "sources.json").read_text(encoding="utf-8"))
    src = {s["id"]: s for s in data["sources"]}
    assert src["chipdip"]["adapter"] == "direct_url" and src["chipdip"]["level"] == "russian"
    assert "static.chipdip.ru" in src["chipdip"]["domains"]
    q = src["russian_queries"]
    text = " ".join(q["queries"])
    for site in ("promelec.ru", "efo.ru", "ozon.ru"):
        assert "site:" + site in text
        assert site in data["pdf_hosts"]
    assert all("{part}" in x for x in q["queries"])
    assert all(v in src for v in q["via"])


def test_world_marketplaces():
    """Шаг 2.13: eBay, Mouser, DigiKey, Farnell, Arrow — запросы с site: у английских поисковиков."""
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data" / "sources.json").read_text(encoding="utf-8"))
    src = {s["id"]: s for s in data["sources"]}
    q = src["world_queries"]
    assert q["adapter"] == "engine_queries" and q["level"] == "world"
    text = " ".join(q["queries"])
    for site in ("mouser.com", "digikey.com", "farnell.com", "arrow.com", "ebay.com"):
        assert "site:" + site in text
        assert site in data["pdf_hosts"]
    assert all("{part}" in x for x in q["queries"])
    assert all(v in src for v in q["via"])


def test_forums_marking_github_sources():
    """Шаг 2.14: форумы и базы SMD-кодов — запросы с site:, GitHub — свой адаптер."""
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "data" / "sources.json").read_text(encoding="utf-8"))
    src = {s["id"]: s for s in data["sources"]}
    forums = " ".join(src["forum_queries"]["queries"])
    for site in ("eevblog.com", "electronix.ru", "radiokot.ru"):
        assert "site:" + site in forums and site in data["pdf_hosts"]
    marking = " ".join(src["marking_queries"]["queries"])
    for site in ("s-manuals.com", "alltransistors.com", "smd.yooneed.one"):
        assert "site:" + site in marking and site in data["pdf_hosts"]
    assert all("{code}" in x for x in src["marking_queries"]["queries"])
    assert src["github"]["adapter"] == "github_api" and "{part}" in src["github"]["url"]
