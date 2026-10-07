# -*- coding: utf-8 -*-
"""Шаг 7.6b: вкладки «Документы» и «Почему» в окне, «Подтвердить»/«Отклонить», «Найти для всех фото», свой PDF."""
import os
import sys

import pytest

pytest.importorskip("PyQt5")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_acquire_store import make_rec  # noqa: E402
from test_latency import _pump  # noqa: E402
from test_models import _png  # noqa: E402
from test_photo_cards import _report  # noqa: E402


def _open(window, tmp_path, names=("a",)):
    w, app = window
    paths = [os.path.normpath(_png(str(tmp_path / (n + ".png")))) for n in names]
    w.add_files(paths)
    _pump(app, lambda: w.list.count() == len(paths) and w.is_idle())
    return w, app, paths


def _two_records(tmp_path):
    a = make_rec(tmp_path, status="probable", name="p.pdf")
    a.verdict.score = 60
    b = make_rec(tmp_path, status="rejected", name="r.pdf", url="https://x.example/r.pdf")
    b.verdict.score = 10
    return a, b


def test_documents_table_and_why(window, tmp_path):
    w, app, (p,) = _open(window, tmp_path)
    r = _report(p, "NE555P", "no")
    r.records = list(_two_records(tmp_path))
    w._analyzed((p, r, []))
    w.list.setCurrentRow(0)
    assert w.docs_table.model().rowCount() == 2
    assert u"выберите документ" in w.why_view.toPlainText().lower()
    w.docs_table.selectRow(0)
    text = w.why_view.toPlainText()
    assert u"Вероятно" in text and u"60" in text and u"NE555P" in text      # итог, баллы, цитата улики
    w.docs_table.selectRow(1)
    assert u"Отклонён" in w.why_view.toPlainText()
    assert [w.tabs.tabText(i) for i in range(4)] == [u"Заключение", u"Документы", u"Почему", u"Журнал"]


def test_other_photo_clears_documents(window, tmp_path):
    w, app, (a, b) = _open(window, tmp_path, ("a", "b"))
    r = _report(a, "NE555P", "no")
    r.records = [_two_records(tmp_path)[0]]
    w._analyzed((a, r, []))
    w.list.setCurrentRow(0)
    assert w.docs_table.model().rowCount() == 1
    w._analyzed((b, _report(b, "LM358", "no"), []))
    w.list.setCurrentRow(1)
    assert w.docs_table.model().rowCount() == 0


def test_accept_and_reject_run_in_background(window, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    w, app, (p,) = _open(window, tmp_path)
    r = _report(p, "NE555P", "no")
    probable, other = _two_records(tmp_path)
    r.records = [probable, other]
    w._analyzed((p, r, []))
    w.list.setCurrentRow(0)
    calls = []

    def decide(rep, rec, accept, progress=None):
        calls.append((rec, accept))
        rec.verdict.status = "confirmed" if accept else "rejected"
        return rec
    monkeypatch.setattr(w.pipe, "decide_record", decide)
    w.decide_doc(True)                                   # документ не выбран — ничего не делает
    assert not calls
    w.docs_table.selectRow(0)
    w.decide_doc(True)
    _pump(app, lambda: w.is_idle() and calls and u"Подтверждён" in w.why_view.toPlainText())
    assert calls == [(probable, True)]
    assert w.docs_model.record(w.docs_table.currentIndex().row()) is probable
    assert u"Подтверждён" in w.why_view.toPlainText()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    w.decide_doc(False)                                  # «Нет» в вопросе — документ не трогается
    assert len(calls) == 1
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w.decide_doc(False)
    _pump(app, lambda: w.is_idle() and len(calls) == 2 and u"Отклонён" in w.why_view.toPlainText())   # обновление — по сигналу
    assert calls[1] == (probable, False) and u"Отклонён" in w.why_view.toPlainText()


def test_find_for_all_photos_in_turn(window, tmp_path, monkeypatch):
    w, app, paths = _open(window, tmp_path, ("a", "b", "c"))
    for p, part in zip(paths, ("NE555P", "LM358", "")):
        w._analyzed((p, _report(p, part, "no"), []))
    seen = []

    def search(r, progress=None, cancel=None, levels=None):
        seen.append(r.chosen_part)
        r.records = [make_rec(tmp_path, status="probable", name=r.chosen_part + ".pdf", part=r.chosen_part)]
    monkeypatch.setattr(w.pipe, "search_web", search)
    w.find_for_all()
    _pump(app, lambda: w.is_idle() and not w.web_queue and len(seen) == 2)
    assert seen == ["NE555P", "LM358"]                   # фото без партномера пропущено


def test_check_own_pdf_adds_record(window, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QFileDialog
    w, app, (p,) = _open(window, tmp_path)
    r = _report(p, "NE555P", "no")
    w._analyzed((p, r, []))
    w.list.setCurrentRow(0)
    pdf = tmp_path / u"мой даташит.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(pdf), "")))
    got = []

    def check(rep, path, progress=None):
        got.append(path)
        rec = make_rec(tmp_path, status="probable", name="own.pdf")
        rep.records.append(rec)
        return rec
    monkeypatch.setattr(w.pipe, "check_own_pdf", check)
    w.check_own_pdf()
    _pump(app, lambda: w.is_idle() and got)
    assert got == [str(pdf)] and w.docs_table.model().rowCount() == 1
    assert w.tabs.currentWidget() is w.why_view


def test_pipeline_decide_and_own_pdf_real(window, tmp_path):
    """Без подмен: решение пользователя переносит файл в библиотеку и ставит datasheet; свой PDF проходит проверку."""
    w, app, (p,) = _open(window, tmp_path)
    r = _report(p, "NE555P", "no")
    rec = make_rec(tmp_path, status="probable", name="real.pdf")
    r.records = [rec]
    out = w.pipe.decide_record(r, rec, True)
    assert out.verdict.status == "confirmed" and r.datasheet_path and os.path.exists(r.datasheet_path)
    assert out.stored_path == r.datasheet_path
    out = w.pipe.decide_record(r, rec, False)
    assert out.verdict.status == "rejected" and r.datasheet_path == ""
    assert "user_rejected" in out.verdict.reasons
    pdf = tmp_path / "own.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
    own = w.pipe.check_own_pdf(r, str(pdf))
    assert own is not None and own in r.records and own.lead.source_id == "manual"
    assert r.to_dict().get("records") is None
