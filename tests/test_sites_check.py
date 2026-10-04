# -*- coding: utf-8 -*-
"""Проверка набора sites: сбор адресов, вердикты, запуск на подмене сети."""
import json
import logging
import os

import pytest
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "win7_pack"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

sites = pytest.importorskip("sites", reason="нет tools/win7_pack (портативная сборка)")
from chipfinder.core.netsafe import SafeHttp  # noqa: E402
from fakes.fake_http import FakeHttp  # noqa: E402

SRC = json.load(open(os.path.join(ROOT, "data", "sources.json"), encoding="utf-8"))


def test_collect_domains_covers_sources():
    hosts = dict(sites.collect_domains(SRC))
    for h in ("html.duckduckgo.com", "www.sogou.com", "www.alldatasheet.com", "www.st.com", "www.ti.com"):
        assert h in hosts
    assert len(hosts) >= 80


def test_collect_searches_three_chips():
    s = sites.collect_searches(SRC)
    assert {c for _, c, _ in s} == set(sites.CHIPS)
    assert any("NE555" in u and "baidu" in u for _, _, u in s)
    assert all("{" not in u for _, _, u in s)


def test_classify_response():
    c = sites.classify_response
    assert c(200, {}, b"<html>ok</html>") == ("ok", "")
    assert c(200, {}, "<title>百度安全验证</title>".encode("utf-8"))[0] == "captcha"
    assert c(403, {"server": "cloudflare"}, b"Just a moment...")[0] == "protection"
    assert c(407, {}, b"")[0] == "network_block"
    assert c(200, {}, b"<h1>Blocked by FortiGuard Web Filter</h1>")[0] == "network_block"
    assert c(404, {}, b"nope") == ("http_error", "HTTP 404")


def test_classify_exception():
    assert sites.classify_exception(Exception("getaddrinfo failed")) == "dns"
    assert sites.classify_exception(Exception("SSLError: certificate verify failed")) == "tls"
    assert sites.classify_exception(Exception("Read timed out")) == "timeout"
    assert sites.classify_exception(Exception("Connection aborted, ConnectionResetError(10054)")) == "reset"


def test_probe_url_never_raises(tmp_path):
    fake = (FakeHttp().add("https://a.com/", "<title>Hi</title>").add_error("https://b.com/", OSError("getaddrinfo failed")))
    http = SafeHttp({"allowed_domains": ["a.com", "b.com"], "min_interval_sec": 0}, str(tmp_path), logging.getLogger("t"),
                    transport=fake)
    ok, body = sites.probe_url(http, "https://a.com/")
    assert ok["verdict"] == "ok" and ok["title"] == "Hi" and body
    bad, _ = sites.probe_url(http, "https://b.com/")
    assert bad["verdict"] == "unreachable:dns"
    skipped, _ = sites.probe_url(http, "https://a.com/", deadline=0)
    assert skipped["verdict"] == "skipped"


def test_run_end_to_end_with_fake(tmp_path, monkeypatch):
    fake = FakeHttp()
    for h, _ in sites.collect_domains(SRC):
        fake.add("https://%s/" % h, "<title>%s</title>" % h)
    fake.add_error("https://www.ti.com/", OSError("Connection reset 10054"))
    for name, chip, url in sites.collect_searches(SRC):
        fake.add(url, "<title>%s</title>" % chip)
    fake.routes["https://www.sogou.com/antispider/"] = fake.routes["https://www.ti.com/"]

    real = SafeHttp

    def factory(cfg, q, log):
        cfg = dict(cfg, min_interval_sec=0)
        return real(cfg, q, log, transport=fake)

    monkeypatch.setattr("chipfinder.core.netsafe.SafeHttp", factory)
    from run_checks import Context
    ctx = Context(ROOT, str(tmp_path), "sites")
    res = sites.run(ctx)
    assert res["status"] == "ok" and res["domains_ok"] >= 80
    data = json.load(open(str(tmp_path / "sites.json"), encoding="utf-8"))
    ti = [r for r in data["domains"] if r["host"] == "www.ti.com"][0]
    assert ti["verdict"] == "unreachable:reset"
    assert os.path.isfile(str(tmp_path / "sites.md"))
    assert os.listdir(str(tmp_path / "pages"))


def test_adapters_check_runs_offline(tmp_path, monkeypatch):
    """Шаг 2.15: проверка adapters проходит весь sources.json без сети и пишет отчёт."""
    import adapters
    import run_checks

    def fake_http(app_dir, hosts, work_dir):
        http = SafeHttp({"min_interval_sec": 0}, str(tmp_path / "q"), logging.getLogger("t"), transport=FakeHttp())
        http.add_allowed(hosts)
        return http, {}
    monkeypatch.setattr(sites, "make_http", fake_http)
    ctx = run_checks.Context(ROOT, str(tmp_path / "w"), "adapters")
    os.makedirs(ctx.work_dir)
    res = adapters.run(ctx)
    assert res["status"] == "ok" and res["sources"] >= 30
    assert "no_adapter" in res["summary"] or "error" in res["summary"]
    for fn in ("adapters.md", "adapters.json", "для_администраторов.txt"):
        assert os.path.isfile(os.path.join(ctx.work_dir, fn))
    assert "adapters" in run_checks.discover(os.path.join(ROOT, "tools", "win7_pack"))
