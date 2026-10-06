# -*- coding: utf-8 -*-
"""Статистика поиска (шаг 5.4, ARCHITECTURE §4.10): запись по событиям, показатели источников. Время сдвигается."""
import logging

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.stats import SearchStats, StatsRecorder
from chipfinder.core.interfaces import Context
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def make(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    clock = Clock()
    stats = SearchStats(db, clock=clock)
    bus = EventBus()
    return stats, StatsRecorder(stats, bus, clock=clock), bus, clock


def run(rec, bus, clock, part, result, source="ddg", lang="en", maker="", links=3, queries=2):
    """Один синтетический поиск: источник делает запросы, находит ссылки, скачивает, итог `result`."""
    rec.begin(part, maker=maker, level="L1")
    for i in range(queries):
        bus.emit("engine.query", lang=lang, source=source, engine=source, query="q%d" % i)
    clock.t += 2
    bus.emit("engine.found", lang=lang, source=source, engine=source, query="q", n=links)
    if result in ("confirmed", "probable", "rejected"):
        bus.emit("fetch.done", lang=lang, source=source, file="a.pdf", size=100)
        if result == "rejected":
            bus.emit("verify.rejected", lang=lang, source=source, part=part, score=1)
        clock.t += 8
    bus.emit("result." + result, queries=queries, seconds=10)


def test_run_is_recorded_from_events(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    run(rec, bus, clock, "W25Q64JVSIQ", "confirmed", maker="Winbond")
    [r] = stats.runs()
    assert r["part"] == "W25Q64JVSIQ" and r["family"] == "W25Q64" and r["maker"] == "Winbond"
    assert r["status"] == "confirmed" and r["first_source"] == "ddg"
    assert r["queries"] == 2 and r["links"] == 3 and r["downloaded"] == 1
    assert r["t_first_link"] == 2 and r["t_confirm"] == 10
    [s] = stats.source_runs()
    assert (s["source"], s["queries"], s["links"], s["downloaded"], s["confirmed"]) == ("ddg", 2, 3, 1, 1)


def test_captcha_errors_and_rejected_are_counted_per_source(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    rec.begin("LM358")
    bus.emit("engine.query", source="bing", query="q")
    bus.emit("engine.captcha", source="bing", query="q", engine="bing", minutes=30)
    bus.emit("engine.error", source="brave", query="q")
    bus.emit("access.site_protected", source="alldatasheet")
    bus.emit("fetch.done", source="x", file="a.pdf", size=1)
    bus.emit("verify.rejected", source="x", part="LM358", score=0)
    bus.emit("result.rejected", queries=1, seconds=3)
    rows = {s["source"]: s for s in stats.source_runs()}
    assert rows["bing"]["captcha"] == 1 and rows["brave"]["errors"] == 1
    assert rows["alldatasheet"]["captcha"] == 1
    assert rows["x"]["rejected"] == 1 and rows["x"]["confirmed"] == 0
    assert stats.runs()[0]["first_source"] == ""


def test_verdict_without_source_goes_to_last_downloaded_source(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    rec.begin("NE555")
    bus.emit("engine.found", source="ddg", n=2)
    bus.emit("fetch.done", source="ddg", file="a.pdf", size=1)
    bus.emit("result.probable", queries=1, seconds=1)
    [s] = stats.source_runs()
    assert s["probable"] == 1 and s["confirmed"] == 0


def test_metrics_overall_and_by_family_and_lang(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    for _ in range(3):
        run(rec, bus, clock, "W25Q64JV", "confirmed", source="ddg", queries=2)
    run(rec, bus, clock, "W25Q64JV", "not_found", source="ddg", queries=2)
    run(rec, bus, clock, "STM32F103C8T6", "not_found", source="baidu", lang="zh", queries=4)
    run(rec, bus, clock, "STM32F103C8T6", "confirmed", source="baidu", lang="zh", queries=4)
    m = {x["source"]: x for x in stats.metrics()}
    assert m["ddg"]["attempts"] == 4 and m["ddg"]["success_rate"] == 0.75
    assert m["ddg"]["avg_confirm_time"] == 10 and m["ddg"]["queries_per_success"] == 8 / 3
    assert m["baidu"]["success_rate"] == 0.5 and m["baidu"]["queries_per_success"] == 8
    by_family = stats.metrics(by="family")
    got = {(x["source"], x["group"]): x["success_rate"] for x in by_family}
    assert got[("ddg", "W25Q64")] == 0.75 and got[("baidu", "STM32F103")] == 0.5
    by_lang = {(x["source"], x["group"]) for x in stats.metrics(by="lang")}
    assert ("baidu", "zh") in by_lang and ("ddg", "en") in by_lang
    assert [x["source"] for x in stats.metrics(source="baidu")] == ["baidu"]


def test_captcha_and_error_rates(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    rec.begin("A1234")
    bus.emit("engine.query", source="bing", query="q")
    bus.emit("engine.captcha", source="bing", query="q")
    bus.emit("result.not_found", queries=1, seconds=1)
    rec.begin("B1234")
    bus.emit("engine.query", source="bing", query="q")
    bus.emit("engine.found", source="bing", n=1)
    bus.emit("result.not_found", queries=1, seconds=1)
    [m] = stats.metrics()
    assert m["captcha_rate"] == 0.5 and m["error_rate"] == 0.0 and m["success_rate"] == 0.0
    assert m["avg_confirm_time"] is None and m["queries_per_success"] is None


def test_search_count_and_fastest_sources(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    assert stats.search_count() == 0
    run(rec, bus, clock, "NE555", "confirmed", source="slow")
    clock.t += 1
    rec.begin("NE556")
    bus.emit("fetch.done", source="fast", file="a.pdf", size=1)
    bus.emit("result.confirmed", queries=1, seconds=1)
    assert stats.search_count() == 2
    assert [x["source"] for x in stats.fastest()] == ["fast", "slow"]


def test_events_without_open_run_are_ignored_and_unfinished_run_is_closed(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    bus.emit("engine.query", source="ddg", query="q")
    assert stats.runs() == []
    rec.begin("A1234")
    bus.emit("engine.query", source="ddg", query="q")
    rec.begin("B1234")                      # прежний поиск не закрыт — записывается как прерванный
    bus.emit("search.cancelled")
    assert [r["status"] for r in stats.runs()] == ["cancelled", "cancelled"]


def test_export_csv(tmp_path):
    stats, rec, bus, clock = make(tmp_path)
    run(rec, bus, clock, "NE555", "confirmed")
    text = stats.export_csv()
    lines = text.strip().splitlines()
    assert lines[0].startswith("source,") and lines[1].startswith("ddg,")
