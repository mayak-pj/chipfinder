# -*- coding: utf-8 -*-
"""Адаптер engine_queries: шаблоны запросов через поисковики (шаг 2.15a)."""
import json
import logging
import os
from urllib.parse import quote_plus

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.engine_queries import EngineQueries, applicable
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(APP, "tests", "fixtures", "sources", "duckduckgo")
DDG = "https://html.duckduckgo.com/html/?q="
BING = "https://www.bing.com/search?q="


def data(queries, via=("ddg", "bing"), **extra):
    return {"version": 2, "levels": [{"id": "search"}, {"id": "forum"}], "sources": [
        {"id": "ddg", "adapter": "engine_html", "name": "DDG", "level": "search", "lang": "en", "decoder": "ddg",
         "url": DDG + "{q}", "domains": ["duckduckgo.com"]},
        {"id": "bing", "adapter": "engine_html", "name": "Bing", "level": "search", "lang": "en", "decoder": "bing",
         "url": "https://www.bing.com/search?q={q}&setlang=en", "domains": ["bing.com"]},
        dict({"id": "qs", "adapter": "engine_queries", "name": "Запросы", "level": "forum", "lang": "",
              "via": list(via), "queries": queries}, **extra)]}


def setup(tmp_path, queries, **kw):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["duckduckgo.com", "bing.com"])
    reg = Registry(data(queries, **kw), bus=bus)
    return reg, http, fake, events


def page(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def test_registered_and_real_sources_json_has_no_missing():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    reg.build()
    assert ADAPTERS["engine_queries"] is EngineQueries and reg.missing == []


def test_applicable_templates():
    t = ["{part} a", "{code} b", "{part} site:{maker_site}"]
    assert applicable(t, "part") == ["{part} a"]
    assert applicable(t, "part", "ti.com") == ["{part} a", "{part} site:{maker_site}"]
    assert applicable(t, "smd") == ["{code} b"]
    assert applicable(t, "") == ["{part} a", "{code} b"]


def test_first_engine_with_results_wins_and_leads_are_retagged(tmp_path):
    reg, http, fake, events = setup(tmp_path, ["{part} datasheet", "{part} форум"])
    for q in ("NE555 datasheet", "NE555 форум"):
        fake.add(DDG + quote_plus(q), page("ok.html"))
    leads = {a.id: a for a in reg.build()}["qs"].search(Query("en", "NE555", "part"), http)
    assert leads and all(l.source_id == "qs" and l.level == "forum" for l in leads)
    assert len(leads) == len(set(l.url for l in leads))                # повторы между запросами убраны
    assert not any(BING in c[1] for c in fake.calls)                  # Bing не трогали: DDG уже дал результат
    assert [e.key for e in events if e.key == "engine.found"] == ["engine.found", "engine.found"]


def test_falls_back_to_next_engine(tmp_path):
    reg, http, fake, events = setup(tmp_path, ["{part} datasheet"])
    fake.add(DDG + quote_plus("NE555 datasheet"), page("empty.html"))
    fake.add(BING + quote_plus("NE555 datasheet") + "&setlang=en", b"<html><ol></ol></html>")
    leads = reg.engine("qs").search(Query("en", "NE555", "part"), http)
    assert leads == []
    assert [e.key for e in events].count("engine.empty") == 2 and len(fake.calls) == 2


def test_smd_and_part_pick_their_templates(tmp_path):
    reg, http, fake, events = setup(tmp_path, ["{part} datasheet", "{code} marking"], via=("ddg",))
    fake.add(DDG + quote_plus("A6W marking"), page("empty.html"))
    reg.engine("qs").search(Query("en", "A6W", "smd"), http)
    assert [c[1] for c in fake.calls] == [DDG + quote_plus("A6W marking")]


def test_no_templates_or_engines_gives_site_empty(tmp_path):
    reg, http, fake, events = setup(tmp_path, ["{code} marking"], via=("ddg",))
    assert reg.engine("qs").search(Query("en", "NE555", "part"), http) == []
    assert [e.key for e in events] == ["site.empty"] and fake.calls == []
    reg, http, fake, events = setup(tmp_path, ["{part} x"], via=("nope",))
    assert reg.engine("qs").search(Query("en", "NE555", "part"), http) == [] and events[-1].key == "site.empty"


def test_disabled_engine_skipped_and_instances_shared(tmp_path):
    reg, http, fake, events = setup(tmp_path, ["{part} x"])
    assert reg.engine("ddg") is reg.engine("ddg") and reg.engine("nope") is None
    reg.data["sources"][0]["enabled"] = False
    reg2 = Registry(json.loads(json.dumps(reg.data)))
    assert reg2.engine("ddg") is None
