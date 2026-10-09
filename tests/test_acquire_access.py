# -*- coding: utf-8 -*-
"""Учёт заблокированных сайтов (шаг 5.6, ARCHITECTURE §4.11): накопление, снятие блокировки, экспорт."""
import csv
import io
import logging

from digger.acquire.access import AccessLog, BLOCKED, OPEN
from digger.acquire.cache import DAY
from digger.core.interfaces import Context
from digger.modules.localdb_sqlite import SQLiteLocalDB


def make(tmp_path):
    cfg = {"paths": {"db": "data/digger.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    t = [1_800_000_000.0]
    return AccessLog(db, clock=lambda: t[0]), t


def test_accumulates_over_searches(tmp_path):
    log, t = make(tmp_path)
    log.record_failure("Example.com", "network_blocked", part="NE555", url="https://example.com/a", level="L1",
                       benefit=0.4)
    t[0] += DAY
    log.record_failure("example.com", "network_blocked", part="LM358", url="https://example.com/b", level="L1",
                       benefit=0.5)
    log.record_failure("example.com", "network_blocked", part="NE555")
    rows = log.entries()
    assert len(rows) == 1
    r = rows[0]
    assert r["domain"] == "example.com" and r["status"] == BLOCKED and r["attempts"] == 3
    assert r["parts"] == ["LM358", "NE555"] and r["first_at"] < r["last_at"]
    assert r["sample_url"] == "https://example.com/a" and r["benefit"] == 0.5 and r["level"] == "L1"


def test_only_network_blocks_are_kept(tmp_path):
    log, _ = make(tmp_path)
    for cls in ("site_protected", "transient", "unknown", "not_whitelisted", ""):
        assert log.record_failure("x.com", cls, part="A") is False
    assert log.entries() == []


def test_open_access_and_reblock(tmp_path):
    log, t = make(tmp_path)
    log.record_failure("a.com", "network_blocked", part="A")
    log.record_failure("b.com", "network_blocked", part="B")
    assert log.record_ok("a.com") is True
    assert log.record_ok("never-blocked.com") is False
    assert [r["domain"] for r in log.entries(BLOCKED)] == ["b.com"]
    assert [r["domain"] for r in log.entries(OPEN)] == ["a.com"]
    assert log.entries(OPEN)[0]["opened_at"] == t[0]
    log.record_failure("a.com", "network_blocked", part="C")     # закрыли снова
    assert log.entries(OPEN) == [] and log.get("a.com")["attempts"] == 2


def test_from_diagnosis(tmp_path):
    log, _ = make(tmp_path)
    log.record_failure("a.com", "network_blocked", part="A")
    log.record_failure("b.com", "network_blocked", part="B")
    log.apply_diagnosis({"a.com": "ok", "b.com": "error", "c.com": "ok"})
    assert log.get("a.com")["status"] == OPEN
    assert log.get("b.com")["status"] == BLOCKED
    assert log.get("c.com") is None


def test_export_txt_and_csv(tmp_path):
    log, _ = make(tmp_path)
    log.record_failure("a.com", "network_blocked", part="NE555", url="https://a.com/x", level="L1", benefit=0.4)
    log.record_failure("a.com", "network_blocked", part="LM358")
    log.record_failure("b.com", "network_blocked", part="W25Q64")
    log.record_ok("b.com")
    txt = log.export_txt(log_hint="logs/digger.log")
    assert "a.com" in txt and "443" in txt and "NE555" in txt and "LM358" in txt
    assert "b.com" not in txt                                  # доступ открыт — в запрос не входит
    assert "только чтение" in txt and "logs/digger.log" in txt
    rows = list(csv.DictReader(io.StringIO(log.export_csv())))
    assert len(rows) == 1 and rows[0]["domain"] == "a.com" and rows[0]["port"] == "443"
    assert rows[0]["attempts"] == "2" and rows[0]["parts"] == "LM358; NE555"


def test_export_empty(tmp_path):
    log, _ = make(tmp_path)
    assert "нет" in log.export_txt().lower()
    assert log.export_csv().splitlines()[0].startswith("domain")
