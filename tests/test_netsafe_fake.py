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

import record_fixture  # noqa: E402


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
    src = tmp_path / "page.html"
    src.write_text("<html><script>evil()</script><style>a{}</style><p>LM358</p></html>", encoding="utf-8")
    out = tmp_path / "out"
    record_fixture.main(["ti", "lm358", "https://www.ti.com/product/LM358", "--from-file", str(src), "--out", str(out)])
    html = (out / "ti" / "lm358.html").read_text(encoding="utf-8")
    assert "LM358" in html and "evil" not in html and "<style" not in html
    meta = json.loads((out / "ti" / "lm358.meta.json").read_text(encoding="utf-8"))
    assert meta["url"].endswith("LM358")
