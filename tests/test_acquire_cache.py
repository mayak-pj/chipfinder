# -*- coding: utf-8 -*-
"""Кэши поиска (шаг 5.3, ARCHITECTURE §4.9): выдача, негативный кэш, здоровье доменов. Время сдвигается."""
import logging

from chipfinder.acquire.cache import AcquireCache, DAY
from chipfinder.acquire.models import Lead
from chipfinder.core.interfaces import Context
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, days: float = 0.0, seconds: float = 0.0) -> None:
        self.t += days * DAY + seconds


def make(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    clock = Clock()
    return AcquireCache(db, clock=clock), clock


def leads():
    return [Lead(url="https://a.com/x.pdf", kind="pdf", source_id="ddg", title="NE555 datasheet"),
            Lead(url="https://b.cn/y", title="数据手册", language="zh")]


def test_search_cache_roundtrip_and_ttl(tmp_path):
    cache, clock = make(tmp_path)
    assert cache.get_search("ddg", "NE555 datasheet") is None
    cache.put_search("ddg", "NE555 datasheet", leads())
    got = cache.get_search("ddg", "NE555 datasheet")
    assert [l.url for l in got] == ["https://a.com/x.pdf", "https://b.cn/y"]
    assert got[1].title == "数据手册" and got[0].kind == "pdf"
    clock.advance(days=6.9)
    assert cache.get_search("ddg", "NE555 datasheet") is not None
    clock.advance(days=0.2)
    assert cache.get_search("ddg", "NE555 datasheet") is None


def test_search_cache_key_ignores_case_and_spaces_but_not_source(tmp_path):
    cache, _ = make(tmp_path)
    cache.put_search("ddg", "NE555  Datasheet", leads())
    assert cache.get_search("ddg", " ne555 datasheet ") is not None
    assert cache.get_search("bing", "ne555 datasheet") is None


def test_search_cache_does_not_store_empty_result(tmp_path):
    cache, _ = make(tmp_path)
    cache.put_search("ddg", "q", [])
    assert cache.get_search("ddg", "q") is None


def test_negative_cache_30_days(tmp_path):
    cache, clock = make(tmp_path)
    assert not cache.is_not_found("NE555P")
    cache.mark_not_found("NE-555 P", reason="no_leads")
    assert cache.is_not_found("ne555p")
    clock.advance(days=29)
    assert cache.is_not_found("NE555P")
    clock.advance(days=2)
    assert not cache.is_not_found("NE555P")


def test_negative_cache_forget(tmp_path):
    cache, _ = make(tmp_path)
    cache.mark_not_found("NE555P")
    cache.forget_not_found("NE555P")
    assert not cache.is_not_found("NE555P")


def test_negative_cache_per_level(tmp_path):
    cache, _ = make(tmp_path)
    cache.mark_not_found("NE555P", level="engines")
    assert cache.is_not_found("NE555P", level="engines")
    assert not cache.is_not_found("NE555P", level="catalogs")


def test_domain_rests_after_failures_and_recovers(tmp_path):
    cache, clock = make(tmp_path)
    assert not cache.is_resting("x.com")
    cache.record_domain("x.com", ok=False, error="timeout")
    assert not cache.is_resting("x.com")           # одна неудача — ещё не отдых
    cache.record_domain("x.com", ok=False, error="timeout")
    assert cache.is_resting("x.com")
    clock.advance(seconds=cache.rest_seconds(2) + 1)
    assert not cache.is_resting("x.com")
    cache.record_domain("x.com", ok=True)
    h = cache.domain_health("x.com")
    assert h["fails"] == 0 and h["ok_total"] == 1 and h["fail_total"] == 2


def test_captcha_rests_immediately_and_longer(tmp_path):
    cache, clock = make(tmp_path)
    cache.record_domain("y.com", ok=False, error="captcha")
    assert cache.is_resting("y.com")
    assert cache.rest_seconds(1, captcha=True) > cache.rest_seconds(1)
    clock.advance(seconds=cache.rest_seconds(1, captcha=True) + 1)
    assert not cache.is_resting("y.com")


def test_rest_grows_with_failures_and_is_capped(tmp_path):
    cache, _ = make(tmp_path)
    assert cache.rest_seconds(2) < cache.rest_seconds(3) < cache.rest_seconds(4)
    assert cache.rest_seconds(50) == cache.rest_seconds(20) <= DAY


def test_success_resets_rest(tmp_path):
    cache, _ = make(tmp_path)
    for _ in range(3):
        cache.record_domain("z.com", ok=False, error="5xx")
    assert cache.is_resting("z.com")
    cache.record_domain("z.com", ok=True)
    assert not cache.is_resting("z.com")


def test_purge_expired(tmp_path):
    cache, clock = make(tmp_path)
    cache.put_search("ddg", "q", leads())
    cache.mark_not_found("A1")
    clock.advance(days=8)
    assert cache.purge() == 1                     # выдача устарела, негативный кэш ещё жив
    assert cache.is_not_found("A1")
    clock.advance(days=25)
    assert cache.purge() == 1


def test_survives_reopen(tmp_path):
    cache, clock = make(tmp_path)
    cache.mark_not_found("NE555P")
    cache.record_domain("x.com", ok=False, error="captcha")
    cache2 = AcquireCache(cache.db, clock=clock)
    assert cache2.is_not_found("NE555P") and cache2.is_resting("x.com")
