# -*- coding: utf-8 -*-
"""Адаптивный порядок источников (шаг 6.3, ARCHITECTURE §4.10): история поисков → порядок, разведка, режимы."""
import logging

from digger.acquire.adaptive import AdaptiveOrder
from digger.acquire.events import Event, render
from digger.acquire.stats import SearchStats, StatsRecorder
from digger.core.interfaces import Context
from digger.modules.localdb_sqlite import SQLiteLocalDB
from tests.fakes.fake_http import FakeHttp
from tests.test_acquire_orchestrator import ALL, TI, build, env, keys, pdf_at, search, source  # noqa: F401

LEVELS = [("catalog", ["cat_a", "cat_b"]), ("maker", ["maker_x"]), ("search", ["ddg", "bing"])]
DEFAULT = ["catalog", "maker", "search"]


def make(tmp_path):
    cfg = {"paths": {"db": "data/digger.sqlite", "library_dir": "lib"}}
    return SearchStats(SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t"))))


def history(stats, n, wins=(), fails=(), family="NE555", seconds=10.0, queries=1):
    """`n` поисков: источники `wins` подтвердили документ за `seconds`, источники `fails` ничего не дали."""
    for _ in range(n):
        rows = [dict(source=s, lang="en", family=family, maker="", queries=queries, links=1, downloaded=1,
                     confirmed=1, probable=0, rejected=0, errors=0, captcha=0, t_first_link=1.0, t_confirm=seconds)
                for s in wins]
        rows += [dict(source=s, lang="en", family=family, maker="", queries=queries, links=0, downloaded=0,
                      confirmed=0, probable=0, rejected=0, errors=0, captcha=0, t_first_link=None, t_confirm=None)
                 for s in fails]
        stats.save_run(dict(started_at=0.0, part=family, family=family, maker="", level="",
                            status="confirmed" if wins else "not_found", first_source=wins[0] if wins else ""), rows)


def test_fixed_until_enough_searches(tmp_path):
    stats = make(tmp_path)
    history(stats, 19, wins=["ddg"], fails=["cat_a", "cat_b", "maker_x"])
    plan = AdaptiveOrder(stats).plan(LEVELS, "NE555P")
    assert not plan.adaptive and plan.searches == 19
    assert plan.levels == DEFAULT and plan.sources == dict(LEVELS)


def test_history_moves_useful_level_and_source_forward(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["bing"], fails=["cat_a", "cat_b", "maker_x", "ddg"])
    plan = AdaptiveOrder(stats, rng=lambda: 0.99).plan(LEVELS, "NE555P")
    assert plan.adaptive and plan.searches == 20 and not plan.explored
    assert plan.levels == ["search", "catalog", "maker"]
    assert plan.sources["search"] == ["bing", "ddg"] and plan.sources["catalog"] == ["cat_a", "cat_b"]


def test_equal_history_keeps_default_order(tmp_path):
    stats = make(tmp_path)
    history(stats, 25, wins=["cat_a", "cat_b", "maker_x", "ddg", "bing"])
    plan = AdaptiveOrder(stats, rng=lambda: 0.99).plan(LEVELS, "NE555P")
    assert plan.adaptive and plan.levels == DEFAULT and plan.sources == dict(LEVELS)


def test_changes_are_gradual(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["cat_a"])                # 20 поисков, остальные источники не спрашивались
    history(stats, 2, wins=["ddg"], fails=["cat_a"])  # две удачи поисковика первый источник ещё не обгоняют
    order = AdaptiveOrder(stats, rng=lambda: 0.99)
    assert order.plan(LEVELS, "NE555P").levels[0] == "catalog"
    history(stats, 20, wins=["ddg"], fails=["cat_a"])
    assert order.plan(LEVELS, "NE555P").levels[0] == "search"


def test_slow_and_costly_source_goes_back(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["cat_a"], seconds=120.0, queries=8)
    history(stats, 20, wins=["cat_b"], seconds=5.0)
    plan = AdaptiveOrder(stats, rng=lambda: 0.99).plan(LEVELS, "NE555P")
    assert plan.sources["catalog"] == ["cat_b", "cat_a"]


def test_family_history_beats_overall(tmp_path):
    stats = make(tmp_path)
    history(stats, 30, wins=["cat_a"], fails=["maker_x"])                      # в целом лучше каталог
    history(stats, 10, wins=["maker_x"], fails=["cat_a"], family="W25Q64")     # для W25Q64 — производитель
    order = AdaptiveOrder(stats, rng=lambda: 0.99)
    assert order.plan(LEVELS, "W25Q64JVSSIQ").levels[0] == "maker"
    assert order.plan(LEVELS, "NE555P").levels[0] == "catalog"


def test_exploration_puts_rare_source_first(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["cat_a"], fails=["cat_b", "maker_x", "ddg"])      # bing ни разу не спрашивали
    plan = AdaptiveOrder(stats, rng=lambda: 0.05).plan(LEVELS, "NE555P")
    assert plan.explored == "bing" and plan.levels[0] == "search" and plan.sources["search"][0] == "bing"
    assert not AdaptiveOrder(stats, rng=lambda: 0.5).plan(LEVELS, "NE555P").explored
    assert not AdaptiveOrder(stats, explore=0, rng=lambda: 0.0).plan(LEVELS, "NE555P").explored


def test_exploration_share_is_about_ten_percent(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["cat_a"])
    order = AdaptiveOrder(stats)
    explored = sum(1 for _ in range(400) if order.plan(LEVELS, "NE555P").explored)
    assert 15 <= explored <= 75


def test_nothing_to_explore_when_all_sources_are_known(tmp_path):
    stats = make(tmp_path)
    history(stats, 20, wins=["cat_a"], fails=["cat_b", "maker_x", "ddg", "bing"])
    assert not AdaptiveOrder(stats, rng=lambda: 0.0).plan(LEVELS, "NE555P").explored


def test_fixed_mode_does_not_change_order(tmp_path):
    stats = make(tmp_path)
    history(stats, 40, wins=["bing"], fails=["cat_a", "cat_b", "maker_x", "ddg"])
    plan = AdaptiveOrder(stats, enabled=False, rng=lambda: 0.0).plan(LEVELS, "NE555P")
    assert not plan.adaptive and plan.levels == DEFAULT and plan.sources == dict(LEVELS) and not plan.explored


def test_local_hits_do_not_count_as_searches(tmp_path):
    stats = make(tmp_path)
    history(stats, 30, wins=["local"])
    assert AdaptiveOrder(stats).plan(LEVELS, "NE555P").searches == 0


def test_order_lines_in_three_languages():
    line = render(Event("search.order_adaptive", params={"n": 37}), "ru")
    assert line == "порядок: адаптивный, по 37 поискам"
    assert "по 1 поиску" in render(Event("search.order_adaptive", params={"n": 1}), "ru")
    for lang in ("en", "zh", "ru"):
        assert "37" in render(Event("search.order_adaptive", params={"n": 37}), lang)
        assert "bing" in render(Event("search.order_explore", params={"name": "bing"}), lang)


# -------------------- оркестратор --------------------
def asked(bus):
    return [e.source for e in bus.history() if e.key == "site.search"]


def orchestrator(env, tmp_path, **adaptive):                      # noqa: F811
    stats = SearchStats(env[1].db)
    fake = pdf_at(FakeHttp(), env[2], TI, ALL % "a")
    orch, bus = build(env, fake, [source("cat", "catalog", ALL % "a"), source("maker", "maker", TI)], parallel=1)
    orch.recorder = StatsRecorder(stats, bus)
    orch.adaptive = AdaptiveOrder(stats, rng=lambda: 0.99, **adaptive)
    return orch, bus, stats


def test_orchestrator_asks_levels_in_adaptive_order(env, tmp_path):   # noqa: F811
    orch, bus, stats = orchestrator(env, tmp_path)
    history(stats, 20, wins=["maker"], fails=["cat"])
    res = search(orch)
    assert res.status == "confirmed" and asked(bus) == ["maker"]      # каталог не понадобился
    line = [e for e in bus.history() if e.key == "search.order_adaptive"]
    assert len(line) == 1 and line[0].params["n"] == 20
    assert stats.search_count() == 21


def test_orchestrator_fixed_mode_keeps_sources_order(env, tmp_path):  # noqa: F811
    orch, bus, stats = orchestrator(env, tmp_path, enabled=False)
    history(stats, 20, wins=["maker"], fails=["cat"])
    search(orch, everywhere=True)
    assert asked(bus) == ["cat", "maker"]
    assert "search.order_adaptive" not in keys(bus)


def test_orchestrator_reports_exploration(env, tmp_path):             # noqa: F811
    orch, bus, stats = orchestrator(env, tmp_path)
    orch.adaptive.rng = lambda: 0.0
    history(stats, 20, wins=["cat"])                                  # «maker» ещё не спрашивали
    search(orch, everywhere=True)
    assert asked(bus) == ["maker", "cat"]
    assert [e.params["name"] for e in bus.history() if e.key == "search.order_explore"] == ["maker"]
