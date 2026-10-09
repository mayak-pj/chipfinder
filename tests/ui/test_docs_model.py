# -*- coding: utf-8 -*-
"""Шаг 7.6a: данные вкладок «Документы» и «Почему», решение пользователя (ARCHITECTURE §4.4, §4.5, §5)."""
import os
import sys

import pytest

pytest.importorskip("PyQt5")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_acquire_store import make_db, make_rec  # noqa: E402

from digger.acquire import review  # noqa: E402
from digger.acquire.models import Evidence  # noqa: E402
from digger.acquire.store import AcquireStore  # noqa: E402
from digger.gui.docs_model import DocsModel, evidence_lines, why_html  # noqa: E402


@pytest.fixture
def store(tmp_path):
    db = make_db(tmp_path)
    yield AcquireStore(db)
    db.close()


def _rec(tmp_path, status, score, name, **kw):
    rec = make_rec(tmp_path, status=status, name=name, **kw)
    rec.verdict.score = score
    return rec


def test_model_orders_and_shows(qapp, tmp_path):
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    colors = {k: QColor("#123456") for k in ("success", "warning", "accent", "danger")}
    m = DocsModel(colors)
    a, b, c = (_rec(tmp_path, "rejected", 10, "a.pdf"), _rec(tmp_path, "probable", 50, "b.pdf"),
               _rec(tmp_path, "confirmed", 90, "c.pdf"))
    m.set_records([a, b, c])
    assert [m.record(i) for i in range(3)] == [c, b, a]
    assert m.data(m.index(0, 0)) == u"Подтверждён" and m.data(m.index(0, 1)) == "90"
    assert m.data(m.index(0, 3)) == u"datasheet" and m.data(m.index(0, 4)) == "3"
    assert m.data(m.index(0, 0), Qt.ForegroundRole) is not None
    assert m.record(5) is None
    seen = []
    m.dataChanged.connect(lambda *a: seen.append(1))
    m.refresh(b)
    assert seen


def test_why_has_quotes_points_and_reasons(tmp_path):
    rec = _rec(tmp_path, "needs_user", 55, "n.pdf")
    rec.verdict.evidence = [Evidence(code="E5", points=15, detail="datasheet"),
                            Evidence(code="E1", points=30, detail=u"NE555P <dual> timer", page=1),
                            Evidence(code="E7", points=-10, detail="DIP-8"), Evidence(code="E12", points=0)]
    rec.verdict.reasons = ["package_conflict", "hard:custom"]
    lines = evidence_lines(rec)
    assert [e.code for e, _t in lines] == ["E1", "E5", "E7", "E12"]
    html = why_html(rec)
    assert u"+30" in html and u"-10" in html and u"стр. 1" in html
    assert u"«NE555P &lt;dual&gt; timer»" in html           # цитата экранирована
    assert u"корпус не совпал" in html and u"custom" in html and u"Нужно ваше решение" in html
    assert u"решите сами" in html and u"ti.com" in html
    assert u"Выберите документ" in why_html(None)


def test_accept_moves_probable_to_confirmed(tmp_path, store):
    rec = make_rec(tmp_path, status="probable")
    old = store.save(rec)
    assert "/probable/" in old.replace("\\", "/")
    review.accept(rec, store)
    assert rec.verdict.status == "confirmed" and "user_confirmed" in rec.verdict.reasons
    assert "/confirmed/" in rec.stored_path.replace("\\", "/") and os.path.isfile(rec.stored_path)
    assert not os.path.exists(old)


def test_accept_needs_user_stores_but_not_hard_reject(tmp_path, store):
    rec = make_rec(tmp_path, status="needs_user")
    review.accept(rec, store)
    assert rec.verdict.status == "confirmed" and os.path.isfile(rec.stored_path)
    bad = make_rec(tmp_path, data=b"%PDF-1.4 other\n" * 9, status="rejected", name="h.pdf")
    bad.verdict.reasons = ["hard:active_content"]
    review.accept(bad, store)
    assert bad.verdict.status == "rejected" and not bad.stored_path


def test_reject_removes_file_and_logs(tmp_path, store):
    rec = make_rec(tmp_path, status="probable")
    path = store.save(rec)
    review.reject(rec, store)
    assert rec.verdict.status == "rejected" and "user_rejected" in rec.verdict.reasons
    assert not os.path.exists(path) and not os.path.exists(path + ".json") and rec.stored_path == ""
    assert store.db.conn.execute("SELECT COUNT(*) FROM files WHERE path=?", (path,)).fetchone()[0] == 0
    assert store.acquisitions("NE555P")[-1]["status"] == "rejected"
    review.accept(rec, store)                       # передумал: файл в карантине ещё есть → снова в библиотеке
    assert rec.verdict.status == "confirmed" and "user_rejected" not in rec.verdict.reasons
    assert os.path.isfile(rec.stored_path)


def test_remove_only_inside_library(tmp_path, store):
    outside = tmp_path / "x.pdf"
    outside.write_bytes(b"%PDF")
    assert store.remove(str(outside)) is False and outside.exists()
