# -*- coding: utf-8 -*-
"""Проверка своего PDF (шаг 5.7, ARCHITECTURE §4.8)."""
import json
import logging
import os

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from digger.acquire import manual as M
from digger.acquire.events import EventBus
from digger.acquire.models import PhotoContext
from digger.acquire.store import AcquireStore
from digger.acquire.verify import SourceTrust
from digger.core.interfaces import Context
from digger.modules.localdb_sqlite import SQLiteLocalDB
from tests.fixtures import make_pdfs

TRUST = SourceTrust(makers=("ti.com",), catalogs=("alldatasheet.com",))


@pytest.fixture()
def env(tmp_path):
    cfg = {"paths": {"db": "data/digger.sqlite", "library_dir": "lib"}}
    db = SQLiteLocalDB({}, Context(cfg, str(tmp_path), logging.getLogger("t")))
    mine = tmp_path / "Загрузки мои"
    mine.mkdir()
    return AcquireStore(db), str(tmp_path / "quarantine"), mine


def ctx(part="NE555P", maker="Texas Instruments", package="DIP-8"):
    return PhotoContext(part=part, manufacturer=maker, package=package)


def run(env, path, context=None, **kw):
    store, quarantine, _ = env
    bus = EventBus()
    rec = M.check_manual_pdf(str(path), context or ctx(), store, quarantine, bus=bus, trust=TRUST, **kw)
    return rec, bus


def test_right_pdf_reaches_library(env):
    _, quarantine, mine = env
    src = make_pdfs.write("datasheet", mine)
    before = open(src, "rb").read()
    rec, bus = run(env, src, url="https://www.ti.com/lit/ds/ne555.pdf")
    assert rec.verdict.status == "confirmed" and rec.stored_path.endswith(".pdf")
    assert "confirmed" in rec.stored_path and "__manual__" in rec.stored_path
    passport = json.load(open(rec.stored_path + ".json", encoding="utf-8"))
    assert passport["source"] == "manual" and passport["manual"] is True
    assert passport["url"] == "https://www.ti.com/lit/ds/ne555.pdf"
    assert open(src, "rb").read() == before                 # файл пользователя не тронут
    assert os.path.isfile(rec.fetch.path_in_quarantine) and rec.fetch.path_in_quarantine.startswith(quarantine)
    keys = [e.key for e in bus.history()]
    assert keys[:3] == ["manual.start", "quarantine.placed", "validate.ok"]


def test_right_pdf_without_url_is_only_probable(env):
    _, _, mine = env
    rec, _ = run(env, make_pdfs.write("datasheet", mine))
    assert rec.verdict.status == "probable" and "unconfirmed" in rec.verdict.reasons
    assert rec.stored_path and "probable" in rec.stored_path


def test_user_confirms(env):
    _, _, mine = env
    rec, _ = run(env, make_pdfs.write("datasheet", mine), user=True)
    assert rec.verdict.status == "confirmed" and "user_confirmed" in rec.verdict.reasons


def test_foreign_pdf_is_rejected(env):
    store, _, mine = env
    rec, _ = run(env, make_pdfs.write("foreign", mine))
    assert rec.verdict.status == "rejected" and rec.stored_path == ""
    assert store.acquisitions("NE555P")[-1]["status"] == "rejected"


def test_dangerous_pdf_is_hard_rejected_even_if_user_confirms(env):
    _, _, mine = env
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w._root_object[NameObject("/OpenAction")] = w._add_object(DictionaryObject({
        NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert(1)")}))
    path = mine / "опасный.pdf"
    with open(str(path), "wb") as f:
        w.write(f)
    rec, bus = run(env, path, url="https://www.ti.com/x.pdf", user=True)
    assert rec.verdict.status == "rejected" and rec.verdict.reasons == ["hard:active_content"]
    assert rec.stored_path == "" and os.path.isfile(rec.fetch.path_in_quarantine)
    assert "validate.active_content" in [e.key for e in bus.history()]


def test_not_a_pdf(env):
    _, _, mine = env
    path = mine / "doc.pdf"
    path.write_bytes(b"MZ\x90\x00 this is an exe" * 20)
    rec, _ = run(env, path)
    assert rec.verdict.status == "rejected" and rec.verdict.reasons == ["hard:not_pdf"] and rec.stored_path == ""


def test_missing_file_does_not_raise(env):
    _, _, mine = env
    rec, _ = run(env, mine / "нет такого.pdf")
    assert rec.verdict.status == "rejected" and rec.verdict.reasons == ["hard:unreadable"]
    assert rec.fetch.ok is False


def test_too_big(env):
    _, _, mine = env
    rec, bus = run(env, make_pdfs.write("datasheet", mine), max_mb=0.0001)
    assert rec.verdict.reasons == ["hard:too_big"] and rec.fetch.path_in_quarantine == ""
    assert "validate.too_big" in [e.key for e in bus.history()]
