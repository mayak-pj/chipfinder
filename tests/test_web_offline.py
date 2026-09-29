# -*- coding: utf-8 -*-
"""Правила безопасности и разбор выдачи поисковиков (без сети)."""
import base64

import pytest

from chipfinder.core.models import Candidate
from chipfinder.core.netsafe import pdf_danger_scan
from chipfinder.modules.web.linkparse import decode_engine_link, extract_links


@pytest.fixture()
def ws(ctx):
    return ctx.modules["web_search"]


@pytest.mark.parametrize("url", [
    "https://www.alldatasheet.com/view.jsp?x=1",
    "https://pdf1.alldatasheet.com/a.pdf",
])
def test_allowed(ws, url):
    assert ws.http.is_allowed(url)


@pytest.mark.parametrize("url", [
    "https://evil-datasheets.xyz/a.pdf",
    "https://alldatasheet.com.evil.ru/a.pdf",
    "http://www.alldatasheet.com/",
    "https://192.168.1.10/a.pdf",
    "file:///C:/Windows/win.ini",
    "https://www.st.com:8443/a.pdf",
])
def test_forbidden(ws, url):
    assert not ws.http.is_allowed(url)


def test_pdf_danger():
    assert "JavaScript" in pdf_danger_scan(b"%PDF-1.4 /OpenAction << /S /JavaScript /JS (app.alert(1)) >>")
    assert pdf_danger_scan(b"%PDF-1.4 /Type /Page /JSONData") == []


def test_engine_links():
    real = "https://www.ti.com/lit/ds/symlink/lm358.pdf"
    ddg = "https://duckduckgo.com/l/?uddg=" + real.replace(":", "%3A").replace("/", "%2F") + "&rut=abc"
    assert decode_engine_link(ddg, "ddg") == real
    b = base64.urlsafe_b64encode(real.encode()).decode().rstrip("=")
    assert decode_engine_link("https://www.bing.com/ck/a?!&&p=x&u=a1" + b + "&ntb=1", "bing") == real
    html = ('<div class="result c-container" mu="https://www.elecfans.com/soft/24c02.pdf">'
            '<h3><a href="http://www.baidu.com/link?url=zz">24C02 数据手册</a></h3></div>')
    links = extract_links(html, "https://www.baidu.com/s?wd=24C02")
    assert any(u == "https://www.elecfans.com/soft/24c02.pdf" for u, _ in links)


def test_score(ws):
    s1 = ws._score("https://www.ti.com/lit/ds/symlink/lm358.pdf", "LM358 datasheet", "LM358", "catalog")
    s2 = ws._score("https://www.eevblog.com/forum/beginners/lm358-question/", "LM358 question", "LM358", "forum")
    s3 = ws._score("https://www.example.com/page", "Cooking recipes", "LM358", "catalog")
    assert s1 > s2 > 0
    assert s3 == 0


def test_offline_search_empty(ws):
    ws.http.cfg["offline"] = True
    assert ws.search([Candidate(part="LM358", score=1.0)]) == []
