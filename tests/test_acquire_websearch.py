# -*- coding: utf-8 -*-
"""Подключение оркестратора к интерфейсу WebSearch (шаг 6.5): хиты из записей, прогресс, отмена, скачивание."""
import threading
import time

import pytest

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.models import AcquisitionRecord, Lead, PhotoContext, Verdict
from chipfinder.acquire.orchestrator import SearchResult
from chipfinder.core.interfaces import CancelToken
from chipfinder.core.models import Candidate
from chipfinder.acquire.websearch import AcquireWebSearch


class FakeOrch:
    """Оркестратор-заглушка: публикует события и отдаёт заранее заданный итог."""

    def __init__(self, result=None, wait=False):
        self.bus = EventBus()
        self.result = result or SearchResult(part="NE555")
        self.contexts = []
        self.everywhere = []
        self.cancelled = threading.Event()
        self.wait = wait

    def search(self, ctx, everywhere=False):
        self.contexts.append(ctx)
        self.everywhere.append(everywhere)
        self.bus.emit("verify.rejected", lang="ru", part=ctx.part, score=3)
        if self.wait:
            self.cancelled.wait(3)
        return self.result

    def cancel(self):
        self.cancelled.set()


def record(url, status, stored="", kind="pdf", score=90, source="alldatasheet"):
    return AcquisitionRecord(part="NE555", lead=Lead(url=url, title="NE555 datasheet", source_id=source,
                                                      level="catalog", kind=kind),
                             verdict=Verdict(status=status, score=score), stored_path=stored)


@pytest.fixture()
def ws(ctx):
    w = ctx.modules["web_search"]
    assert isinstance(w, AcquireWebSearch)
    return w


def use(ws, orch):
    ws._orch = orch
    return orch


def test_allowed_domains(ws):
    assert ws.http.is_allowed("https://www.alldatasheet.com/view.jsp?x=1")
    assert not ws.http.is_allowed("https://alldatasheet.com.evil.ru/a.pdf")
    assert not ws.http.is_allowed("http://www.alldatasheet.com/")


def test_offline_search_empty(ws):
    orch = use(ws, FakeOrch())
    said = []
    assert ws.search([Candidate(part="LM358", score=1.0)], progress=said.append) == []
    assert orch.contexts == [] and said


def test_context_from_candidates(ws):
    ws.http.cfg["offline"] = False
    orch = use(ws, FakeOrch())
    cands = [Candidate(part="NE555", score=0.9, manufacturer="TI"),
             Candidate(part="A7", score=0.5, is_marking_code=True)]
    ws.search(cands, levels=["all"])
    assert orch.contexts[0] == PhotoContext(part="NE555", manufacturer="TI", marking="A7")
    assert orch.everywhere == [True]


def test_hits_from_records_and_progress(ws):
    ws.http.cfg["offline"] = False
    res = SearchResult(part="NE555", status="confirmed", path="/lib/ne555.pdf", records=[
        record("https://www.ti.com/lit/ds/ne555.pdf", "confirmed", stored="/lib/ne555.pdf", score=95),
        record("https://www.alldatasheet.com/ne555.pdf", "probable", score=60),
        record("https://evil.example/ne555.pdf", "rejected", score=5),
        record("https://www.alldatasheet.com/view?x=1", "probable", kind="page", score=50)])
    orch = use(ws, FakeOrch(res))
    said = []
    hits = ws.search([Candidate(part="NE555", score=1.0)], progress=said.append)
    assert said and "NE555" in said[0]
    by_url = {h.location: h for h in hits}
    local = by_url["/lib/ne555.pdf"]
    assert local.is_local and local.is_pdf and local.score == 1.0   # тот же файл, что и `res.path`: лучшая оценка
    probable = by_url["https://www.alldatasheet.com/ne555.pdf"]
    assert not probable.is_local and probable.is_pdf and probable.allowed and probable.score == pytest.approx(0.6)
    assert not by_url["https://www.alldatasheet.com/view?x=1"].is_pdf
    assert "https://evil.example/ne555.pdf" not in by_url          # отклонённые не показываются
    assert hits[0] is local                                         # порядок по оценке
    assert orch.bus is not None


def test_library_hit_without_records(ws):
    ws.http.cfg["offline"] = False
    use(ws, FakeOrch(SearchResult(part="NE555", status="confirmed", reason="local", path="/lib/x.pdf")))
    hits = ws.search([Candidate(part="NE555", score=1.0)])
    assert [(h.location, h.is_local, h.level) for h in hits] == [("/lib/x.pdf", True, "local")]


def test_conclusion_text_goes_to_progress(ws):
    ws.http.cfg["offline"] = False

    class C:
        def text(self):
            return "Не найдено автоматически."

    res = SearchResult(part="NE555", conclusion=C())
    use(ws, FakeOrch(res))
    said = []
    assert ws.search([Candidate(part="NE555", score=1.0)], progress=said.append) == []
    assert "Не найдено автоматически." in said


def test_cancel_token_stops_orchestrator(ws):
    ws.http.cfg["offline"] = False
    orch = use(ws, FakeOrch(wait=True))
    token = CancelToken()
    threading.Timer(0.2, token.cancel).start()
    t0 = time.time()
    ws.search([Candidate(part="NE555", score=1.0)], cancel=token)
    assert orch.cancelled.is_set() and time.time() - t0 < 2


def test_download_refuses_unlisted_site(ws):
    from chipfinder.core.models import DatasheetHit
    h = DatasheetHit(part="NE555", title="x", location="https://evil.example/a.pdf", source="s", level="catalog",
                     allowed=False, is_pdf=True)
    res = ws.download(h)
    assert not res.ok and "белом списке" in res.message


def test_diagnose_lists_sources(ws):
    ws.http.probe = lambda url: (True, "OK")
    rows = ws.diagnose()
    assert rows and all(r["ok"] and r["domain"] for r in rows)
    assert any(r["domain"].endswith("alldatasheet.com") for r in rows)
