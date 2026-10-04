# -*- coding: utf-8 -*-
"""Скачивание в карантин (шаг 3.1)."""
import hashlib
import logging
import os

from chipfinder.acquire.events import EventBus
from chipfinder.acquire.fetch import fetch_to_quarantine
from chipfinder.acquire.models import Lead
from chipfinder.core.netsafe import SafeHttp
from tests.fakes.fake_http import FakeHttp

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
URL = "https://www.ti.com/lit/ds/ne555.pdf"


def make(tmp_path, fake, **net):
    cfg = {"allowed_domains": ["ti.com"], "min_interval_sec": 0}
    cfg.update(net)
    return SafeHttp(cfg, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)


def run(tmp_path, fake, url=URL, **net):
    bus = EventBus()
    pauses = []
    res = fetch_to_quarantine(make(tmp_path, fake, **net), Lead(url=url), bus=bus, pause=0.5, sleep=pauses.append)
    return res, [e.key for e in bus.history()], pauses


def test_success(tmp_path):
    fake = FakeHttp().add(URL, PDF, content_type="application/pdf")
    res, keys, pauses = run(tmp_path, fake)
    assert res.ok and res.size == len(PDF) and res.final_url == URL
    assert res.sha256 == hashlib.sha256(PDF).hexdigest()
    assert os.path.isfile(res.path_in_quarantine) and res.path_in_quarantine.endswith(".pdf.quarantine")
    assert keys == ["fetch.start", "fetch.done", "quarantine.placed"] and pauses == []


def test_redirect_inside_whitelist(tmp_path):
    fake = FakeHttp().add_redirect("https://ti.com/x", URL).add(URL, PDF, content_type="application/pdf")
    res, _, _ = run(tmp_path, fake, url="https://ti.com/x")
    assert res.ok and res.final_url == URL


def test_redirect_to_foreign_domain(tmp_path):
    fake = FakeHttp().add_redirect(URL, "https://evil.example/a.pdf").add("https://evil.example/a.pdf", PDF)
    res, keys, _ = run(tmp_path, fake)
    assert not res.ok and res.failure_class == "not_whitelisted" and keys[-1] == "fetch.failed"
    assert len(fake.calls) == 1


def test_too_big(tmp_path):
    fake = FakeHttp().add(URL, PDF + b"0" * 2 * 1024 * 1024, content_type="application/pdf")
    res, keys, _ = run(tmp_path, fake, max_pdf_mb=1)
    assert not res.ok and "лимит" in res.error and len(fake.calls) == 1   # без повтора
    assert os.listdir(str(tmp_path / "q")) == []


def test_html_instead_of_pdf(tmp_path):
    fake = FakeHttp().add(URL, "<html>Please log in</html>")
    res, keys, _ = run(tmp_path, fake)
    assert not res.ok and "не PDF" in res.error
    assert len(fake.calls) == 1 and os.listdir(str(tmp_path / "q")) == []


def test_5xx_retried_then_ok(tmp_path):
    bad = FakeHttp().add(URL, "oops", status=503)
    good = FakeHttp().add(URL, PDF, content_type="application/pdf")
    calls = []

    def transport(method, url, headers):
        calls.append(url)
        return (bad if len(calls) <= 2 else good)(method, url, headers)
    res, _, pauses = run(tmp_path, transport)
    assert res.ok and pauses == [0.5, 0.5]


def test_5xx_gives_up(tmp_path):
    fake = FakeHttp().add(URL, "oops", status=502)
    res, keys, pauses = run(tmp_path, fake)
    assert not res.ok and res.failure_class == "transient"
    assert len(fake.calls) == 3 and pauses == [0.5, 0.5]


def test_connection_error_retried(tmp_path):
    fake = FakeHttp().add_error(URL, ConnectionError("reset"))
    res, _, _ = run(tmp_path, fake)
    assert not res.ok and len(fake.calls) == 3 and res.failure_class == "transient"


def test_4xx_not_retried(tmp_path):
    fake = FakeHttp().add(URL, "nope", status=404)
    res, _, pauses = run(tmp_path, fake)
    assert not res.ok and len(fake.calls) == 1 and pauses == [] and "404" in res.error
    assert res.failure_class == ""


def test_403_is_site_protected(tmp_path):
    fake = FakeHttp().add(URL, "nope", status=403)
    res, _, _ = run(tmp_path, fake)
    assert res.failure_class == "site_protected" and len(fake.calls) == 1
