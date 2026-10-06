# -*- coding: utf-8 -*-
"""Решения пользователя и доверие к доменам (шаг 5.8, ARCHITECTURE §4.5, §4.4 E9)."""
import json
import logging
import os

import pytest

from chipfinder.acquire import manual as M
from chipfinder.acquire.learn import Learner, domain_of
from chipfinder.acquire.models import PhotoContext
from chipfinder.acquire.store import AcquireStore
from chipfinder.acquire.verify import SourceTrust
from chipfinder.core.interfaces import Context
from chipfinder.modules.localdb_sqlite import SQLiteLocalDB
from tests.fixtures import make_pdfs

TRUST = SourceTrust(makers=("ti.com",), catalogs=("alldatasheet.com",))


@pytest.fixture()
def env(tmp_path):
    cfg = {"paths": {"db": "data/chipfinder.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    store = AcquireStore(db)
    return store, Learner(store, clock=lambda: 1_800_000_000.0), tmp_path


def add(env, n, url):
    """Кладёт в библиотеку файл №n (разный sha) с данного адреса; возвращает запись."""
    store, _, tmp = env
    path = tmp / ("d%d.pdf" % n)
    path.write_bytes(make_pdfs.datasheet() + b"\n%% copy %d\n" % n)
    rec = M.check_manual_pdf(str(path), PhotoContext(part="NE555P", manufacturer="Texas Instruments",
                                                     package="DIP-8"), store, str(tmp / "q"), url=url, trust=TRUST)
    assert rec.verdict.status in ("probable", "confirmed"), rec.verdict.reasons
    return rec


def test_domain_of():
    assert domain_of("https://www.Example.com/a.pdf") == "example.com"
    assert domain_of("") == ""


def test_confirm_moves_file_and_marks_passport(env):
    store, learn, _ = env
    rec = add(env, 1, "https://some-site.org/ne555.pdf")
    sha = rec.fetch.sha256
    assert "probable" in rec.stored_path
    passport = learn.confirm(sha)
    assert passport["verdict"] == "confirmed" and passport["user_decision"] == "confirmed"
    assert "user" in passport["confirmed_by"]
    row = store.db.conn.execute("SELECT path FROM files WHERE sha256=?", (sha,)).fetchone()
    assert "confirmed" in row[0] and os.path.isfile(row[0]) and os.path.isfile(row[0] + ".json")
    assert not os.path.exists(rec.stored_path) and not os.path.exists(rec.stored_path + ".json")
    assert json.load(open(row[0] + ".json", encoding="utf-8"))["user_decision"] == "confirmed"
    assert store.acquisitions("NE555P")[-1]["status"] == "confirmed"
    assert learn.counts("some-site.org") == {"confirmed": 1, "rejected": 0}


def test_reject_removes_from_library(env):
    store, learn, _ = env
    rec = add(env, 1, "https://some-site.org/ne555.pdf")
    sha = rec.fetch.sha256
    passport = learn.reject(sha)
    assert passport["user_decision"] == "rejected"
    assert store.db.conn.execute("SELECT COUNT(*) FROM files WHERE sha256=?", (sha,)).fetchone()[0] == 0
    assert store.db.conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0
    assert not os.path.exists(rec.stored_path)
    moved = os.path.join(store.db.library_dir, "rejected", *os.path.relpath(
        rec.stored_path, store.db.library_dir).replace("\\", "/").split("/")[1:])
    assert os.path.isfile(moved) and os.path.isfile(moved + ".json")
    assert store.acquisitions("NE555P")[-1]["status"] == "rejected"
    assert learn.is_rejected(sha) is True


def test_last_decision_wins(env):
    _, learn, _ = env
    sha = add(env, 1, "https://some-site.org/a.pdf").fetch.sha256
    learn.reject(sha)
    assert learn.confirm(sha) is None            # из библиотеки убран — подтверждать нечего
    assert learn.counts("some-site.org") == {"confirmed": 0, "rejected": 1}


def test_unknown_file(env):
    _, learn, _ = env
    assert learn.confirm("0" * 64) is None and learn.reject("0" * 64) is None
    assert learn.is_rejected("0" * 64) is False


def test_bad_domain_after_two_rejects(env):
    _, learn, _ = env
    learn.reject(add(env, 1, "https://junk.example/a.pdf").fetch.sha256)
    assert learn.trust(TRUST).bad == ()          # одного отказа мало
    learn.reject(add(env, 2, "https://www.junk.example/b.pdf").fetch.sha256)
    t = learn.trust(TRUST)
    assert "junk.example" in t.bad and t.makers == TRUST.makers and t.catalogs == TRUST.catalogs
    assert TRUST.bad == ()                       # исходная база не изменена


def test_confirms_outweigh_rejects(env):
    _, learn, _ = env
    for n in range(1, 4):
        learn.confirm(add(env, n, "https://mixed.example/%d.pdf" % n).fetch.sha256)
    for n in range(4, 6):
        learn.reject(add(env, n, "https://mixed.example/%d.pdf" % n).fetch.sha256)
    assert learn.trust(TRUST).bad == ()          # 2 отказа при 3 подтверждениях — не «плохой»
    assert "mixed.example" not in learn.trust(TRUST).catalogs


def test_good_domain_becomes_catalog_not_maker(env):
    _, learn, _ = env
    for n in range(1, 4):
        learn.confirm(add(env, n, "https://good.example/%d.pdf" % n).fetch.sha256)
    t = learn.trust(TRUST)
    assert "good.example" in t.catalogs and "good.example" not in t.makers
    assert "alldatasheet.com" in t.catalogs


def test_maker_never_becomes_bad(env):
    _, learn, _ = env
    for n in range(1, 3):
        learn.reject(add(env, n, "https://www.ti.com/%d.pdf" % n).fetch.sha256)
    assert learn.trust(TRUST).bad == () and "ti.com" in learn.domains()


def test_forget_domain(env):
    _, learn, _ = env
    for n in range(1, 3):
        learn.reject(add(env, n, "https://junk.example/%d.pdf" % n).fetch.sha256)
    assert learn.forget_domain("Junk.example") == 2
    assert learn.trust(TRUST).bad == () and learn.domains() == {}
    assert learn.is_rejected(add(env, 3, "https://x.example/c.pdf").fetch.sha256) is False


def test_decisions_survive_restart(env):
    store, learn, _ = env
    learn.reject(add(env, 1, "https://junk.example/a.pdf").fetch.sha256)
    learn.reject(add(env, 2, "https://junk.example/b.pdf").fetch.sha256)
    again = Learner(store)
    assert again.bad_domains() == ["junk.example"]
