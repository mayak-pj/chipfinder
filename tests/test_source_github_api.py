# -*- coding: utf-8 -*-
"""Адаптер GitHub Search API (шаг 2.14)."""
import json
import logging
import os

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.query import Query
from chipfinder.acquire.registry import ADAPTERS, Registry
from chipfinder.acquire.sources.base import SourceEntry
from chipfinder.acquire.sources.github_api import GithubApi
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://api.github.com/search/repositories?q=NE555+in:name,description,readme&per_page=10"


def make(tmp_path):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    fake = FakeHttp()
    http = SafeHttp({"min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"), transport=fake)
    http.add_allowed(["api.github.com"])
    entry = [s for s in Registry.load(os.path.join(APP, "data", "sources.json")).data["sources"] if s["id"] == "github"][0]
    return GithubApi(SourceEntry.from_dict(entry), bus=bus), http, fake, events


def test_registered_and_in_sources_json():
    reg = Registry.load(os.path.join(APP, "data", "sources.json"))
    assert ADAPTERS["github_api"] is GithubApi
    assert [a.id for a in reg.build() if a.adapter == "github_api"] == ["github"]


def test_parse_fixture(tmp_path):
    ad, http, fake, events = make(tmp_path)
    with open(os.path.join(APP, "tests", "fixtures", "sources", "github_api", "ok.json"), "rb") as f:
        fake.add(URL, f.read(), content_type="application/json")
    leads = ad.search(Query("en", "NE555", "part"), http)
    assert [l.url for l in leads] == ["https://github.com/example/ne555-docs", "https://github.com/example/timers"]
    assert leads[0].snippet == "NE555 datasheets and notes" and leads[1].snippet == ""
    assert all(l.kind == "page" and l.source_id == "github" and l.level == "github" for l in leads)
    assert events[-1].key == "site.found" and events[-1].params["n"] == 2


def test_empty_and_blank_query(tmp_path):
    ad, http, fake, events = make(tmp_path)
    fake.add(URL, json.dumps({"total_count": 0, "items": []}), content_type="application/json")
    assert ad.search(Query("en", "NE555", "part"), http) == []
    assert events[-1].key == "site.empty"
    assert ad.find("", http) == []


def test_rate_limit_and_errors(tmp_path):
    for status, key in ((403, "engine.quota"), (429, "engine.quota"), (500, "engine.error")):
        ad, http, fake, events = make(tmp_path)
        fake.add(URL, json.dumps({"message": "API rate limit exceeded"}), status=status, content_type="application/json")
        assert ad.search(Query("en", "NE555", "part"), http) == []
        assert events[-1].key == key
    ad, http, fake, events = make(tmp_path)
    fake.add(URL, "<html>no</html>")
    assert ad.search(Query("en", "NE555", "part"), http) == []
    assert events[-1].key == "engine.error"
