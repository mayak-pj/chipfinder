# -*- coding: utf-8 -*-
"""Поисковики с HTML-выдачей (ARCHITECTURE §4.2, шаги 2.2–2.6): один адаптер, декодер выдачи — по полю `decoder`.

Сейчас есть декодер `ddg` (DuckDuckGo HTML). Остальные (bing, mojeek, yandex, baidu…) добавляются в `DECODERS`.
Капча: домен «отдыхает» `REST_MINUTES` минут, в сеть в это время не ходим, событие `engine.captcha`.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
import time
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

from ..models import Lead
from ..registry import register
from .base import SourceAdapter, SourceError

log = logging.getLogger("chipfinder.acquire.engine_html")
REST_MINUTES = 15
_TAG = re.compile(r"<[^>]+>")
_DDG_RESULT = re.compile(r'<a\b[^>]*\bclass="[^"]*\bresult__a\b[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_DDG_SNIPPET = re.compile(r'<a\b[^>]*\bclass="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_HREF = re.compile(r'\bhref="([^"]*)"', re.I)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(_TAG.sub("", fragment))).strip()


def ddg_target(href: str) -> str:
    """Настоящий адрес из ссылки DDG `//duckduckgo.com/l/?uddg=<URL>&rut=…`; рекламу и внутренние ссылки — пусто."""
    href = htmllib.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.scheme not in ("http", "https"):
        return ""
    if parts.hostname and parts.hostname.endswith("duckduckgo.com"):
        if parts.path.startswith("/l/"):
            target = parse_qs(parts.query).get("uddg", [""])[0]
            return unquote(target) if urlsplit(unquote(target)).scheme in ("http", "https") else ""
        return ""
    return href


def ddg_is_captcha(status_html: str) -> bool:
    return "anomaly-modal" in status_html or "anomaly.js" in status_html


def decode_ddg(page: str) -> List[Tuple[str, str, str]]:
    """[(url, заголовок, фрагмент)] из выдачи html.duckduckgo.com в порядке выдачи."""
    out = []
    for block in re.split(r'(?=<div[^>]+class="[^"]*\bresult\b[^"]*")', page):
        m = _DDG_RESULT.search(block)
        if not m:
            continue
        href = _HREF.search(m.group(0))
        url = ddg_target(href.group(1) if href else "")
        if not url:
            continue
        s = _DDG_SNIPPET.search(block)
        out.append((url, _text(m.group(1)), _text(s.group(1)) if s else ""))
    return out


# decoder -> (разбор страницы, признак капчи)
DECODERS: Dict[str, Tuple[Callable[[str], List[Tuple[str, str, str]]], Callable[[str], bool]]] = {
    "ddg": (decode_ddg, ddg_is_captcha),
}


@register
class EngineHtml(SourceAdapter):
    adapter = "engine_html"
    family = "engine"
    kinds = ("pdf", "page")

    def __init__(self, *args: Any, clock: Callable[[], float] = time.time, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._rest_until = 0.0

    def find(self, query: Any, http: Any) -> List[Lead]:
        text = getattr(query, "text", query) or ""
        decoder = self.entry.options.get("decoder", "plain")
        if decoder not in DECODERS:
            raise SourceError("engine.error", "нет декодера «%s»" % decoder)
        parse, is_captcha = DECODERS[decoder]
        left = self._rest_until - self._clock()
        if left > 0:
            raise SourceError("engine.captcha", "отдых", minutes=int(-(-left // 60)))
        url = self.entry.options["url"].format(q=quote_plus(text))
        try:
            fetched = http.fetch(url)
        except Exception as e:
            detail = str(e) or type(e).__name__
            log.warning("%s: %s", self.id, detail)
            raise SourceError("engine.error", detail)
        page = fetched["body"].decode("utf-8", errors="replace")
        if is_captcha(page):
            self._rest_until = self._clock() + REST_MINUTES * 60
            raise SourceError("engine.captcha", "captcha", minutes=REST_MINUTES)
        if fetched["status"] >= 400:
            raise SourceError("engine.quota" if fetched["status"] == 429 else "engine.error",
                              "HTTP %d" % fetched["status"])
        leads = []
        for link, title, snippet in parse(page):
            is_pdf = urlsplit(link).path.lower().endswith(".pdf")
            leads.append(Lead(url=link, title=title, snippet=snippet, kind="pdf" if is_pdf else "page"))
        return leads
