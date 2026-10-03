# -*- coding: utf-8 -*-
"""SafeHttp с подменой сети: редиректы, ошибки, запись фикстур."""
import json
import os
import sys

import pytest

from chipfinder.core.netsafe import NetBlocked, SafeHttp
from fakes.fake_http import FakeHttp

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tools"))


def make(tmp_path, fake):
    cfg = {"allowed_domains": ["ti.com"], "min_interval_sec": 0, "offline": False}
    import logging
    return SafeHttp(cfg, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)


def test_fake_ok(tmp_path):
    fake = FakeHttp().add("https://www.ti.com/a", "<html>привет</html>")
    _, text = make(tmp_path, fake).get_html("https://www.ti.com/a")
    assert "привет" in text


def test_redirect_inside_allowed(tmp_path):
    fake = FakeHttp().add_redirect("https://www.ti.com/a", "/b").add("https://www.ti.com/b", "ok")
    final, text = make(tmp_path, fake).get_html("https://www.ti.com/a")
    assert final == "https://www.ti.com/b" and text == "ok"


def test_redirect_to_foreign_domain_blocked(tmp_path):
    fake = FakeHttp().add_redirect("https://www.ti.com/a", "https://evil.example/x").add("https://evil.example/x", "bad")
    with pytest.raises(NetBlocked):
        make(tmp_path, fake).get_html("https://www.ti.com/a")
    assert all("evil" not in u for _, u in fake.calls)


def test_http_error_and_exception(tmp_path):
    fake = FakeHttp().add("https://www.ti.com/a", "x", status=503).add_error("https://www.ti.com/b", TimeoutError("t"))
    h = make(tmp_path, fake)
    with pytest.raises(NetBlocked):
        h.get_html("https://www.ti.com/a")
    with pytest.raises(TimeoutError):
        h.get_html("https://www.ti.com/b")


def test_record_fixture_from_file(tmp_path):
    record_fixture = pytest.importorskip("record_fixture", reason="нет tools/ (портативная сборка)")
    src = tmp_path / "page.html"
    src.write_text("<html><script>evil()</script><style>a{}</style><p>LM358</p></html>", encoding="utf-8")
    out = tmp_path / "out"
    record_fixture.main(["ti", "lm358", "https://www.ti.com/product/LM358", "--from-file", str(src), "--out", str(out)])
    html = (out / "ti" / "lm358.html").read_text(encoding="utf-8")
    assert "LM358" in html and "evil" not in html and "<style" not in html
    meta = json.loads((out / "ti" / "lm358.meta.json").read_text(encoding="utf-8"))
    assert meta["url"].endswith("LM358")


def test_probe_big_page_is_truncated_not_failed(tmp_path):
    fake = FakeHttp().add("https://www.ti.com/big", b"x" * (900 * 1024))
    ok, info = make(tmp_path, fake).probe("https://www.ti.com/big")
    assert ok and "HTTP 200" in info


def test_limit_message_in_kb_for_small_limits(tmp_path):
    fake = FakeHttp().add("https://www.ti.com/big", b"x" * (900 * 1024))
    with pytest.raises(NetBlocked) as e:
        make(tmp_path, fake)._request("https://www.ti.com/big", 512 * 1024)
    assert "0 МБ" not in str(e.value) and "512 КБ" in str(e.value)


def test_http_redirect_upgraded_to_https(tmp_path):
    """Sogou уводит на http://…/antispider — переходим по https, а не отказываем."""
    cfg = {"allowed_domains": ["sogou.com"], "min_interval_sec": 0}
    import logging
    fake = (FakeHttp().add_redirect("https://www.sogou.com/web", "http://www.sogou.com/antispider/?m=1")
            .add("https://www.sogou.com/antispider/?m=1", "captcha"))
    http = SafeHttp(cfg, str(tmp_path / "q"), logging.getLogger("t"), transport=fake)
    final, text = http.get_html("https://www.sogou.com/web")
    assert final.startswith("https://") and text == "captcha"
    assert all(u.startswith("https://") for _, u in fake.calls)


def test_http_redirect_to_foreign_http_still_blocked(tmp_path):
    fake = FakeHttp().add_redirect("https://www.ti.com/a", "http://evil.example/x")
    with pytest.raises(NetBlocked):
        make(tmp_path, fake).get_html("https://www.ti.com/a")


def test_fetch_returns_status_and_truncates(tmp_path):
    fake = FakeHttp().add("https://www.ti.com/p", b"y" * 5000, status=403)
    r = make(tmp_path, fake).fetch("https://www.ti.com/p", max_bytes=1000)
    assert r["status"] == 403 and len(r["body"]) == 1000 and r["truncated"]
