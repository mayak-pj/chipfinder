# -*- coding: utf-8 -*-
"""Разбор HTML-страниц: извлечение ссылок, раскодирование переадресаций поисковиков."""
from __future__ import annotations

import base64
import re
from typing import List, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

ANCHOR_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text or "")).strip()


def extract_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """[(абсолютный_url, текст_ссылки)]. Скрипты страницы не выполняются."""
    out = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        for a in soup.find_all("a", href=True):
            out.append((urljoin(base_url, a["href"].strip()), a.get_text(" ", strip=True)))
        # Baidu хранит настоящий адрес результата в атрибуте mu
        for div in soup.find_all(attrs={"mu": True}):
            title = div.find(["h3", "a"])
            out.append((div["mu"], title.get_text(" ", strip=True) if title else ""))
    else:
        for href, text in ANCHOR_RE.findall(html):
            out.append((urljoin(base_url, href.strip()), _clean(text)))
        for mu in re.findall(r'\bmu="(https?://[^"]+)"', html):
            out.append((mu, ""))
    return [(u, t) for u, t in out if u.startswith(("http://", "https://"))]


def decode_engine_link(url: str, decoder: str) -> str:
    """Поисковики оборачивают ссылки в переадресации. Раскрываем их без запросов к сети."""
    try:
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        if decoder == "ddg" and "uddg" in q:
            return unquote(q["uddg"][0])
        if decoder == "bing" and parts.path.startswith("/ck/") and "u" in q:
            u = q["u"][0]
            if u.startswith("a1"):
                b = u[2:]
                b += "=" * (-len(b) % 4)
                return base64.urlsafe_b64decode(b).decode("utf-8", "replace")
        if decoder == "google" and parts.path == "/url" and "q" in q:
            return q["q"][0]
    except Exception:  # noqa
        pass
    return url


ENGINE_SELF = ("duckduckgo.com", "bing.com", "baidu.com", "yandex.ru", "sogou.com", "so.com",
               "mojeek.com", "search.brave.com", "google.com", "microsoft.com", "msn.com",
               "yandex.net", "ya.ru", "bdimg.com", "bdstatic.com", "go.microsoft.com")


def is_engine_internal(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ENGINE_SELF)
