# -*- coding: utf-8 -*-
"""Правила безопасности: белый список и проверка PDF (без сети)."""
import pytest

from chipfinder.core.netsafe import pdf_danger_scan


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
