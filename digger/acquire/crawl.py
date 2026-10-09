# -*- coding: utf-8 -*-
"""Обход страниц (ARCHITECTURE §4.2, шаг 3.3): страница → ссылки на PDF.

Со страницы берутся `a[href]`, `iframe[src]`, `embed[src]`, `object[data]`. Ссылка на PDF — путь на `.pdf`,
`type="application/pdf"` или просмотрщик с адресом файла в параметре (`viewer.html?file=…pdf`). Если PDF на
странице нет, открываются вложенные страницы: рамки и ссылки с признаком документа («datasheet», «скачать»,
«数据手册»…) — до глубины `MAX_DEPTH` (стартовая страница — глубина 1) и не больше `MAX_PAGES` страниц.

Сеть — только через `SafeHttp.get_html`: страницы вне белого списка не открываются (в том числе после
редиректа). Ссылка на PDF с чужого домена остаётся в ответе после своих: скачивание даст `not_whitelisted`,
и она попадёт в заключение (§4.11). Функция ничего не скачивает и не бросает исключений.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any, Callable, List, Optional, Tuple
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

from ..core.netsafe import host_of
from .events import EventBus, search_language
from .models import Lead
from .netdiag import BlockTracker, failure_class
from .rank import normalize_url

log = logging.getLogger("digger.acquire.crawl")
MAX_DEPTH = 2
MAX_PAGES = 6
_FRAME_ATTR = {"iframe": "src", "embed": "src", "object": "data"}
_HINT = re.compile(r"datasheet|data[\s_-]?sheet|download|\bpdf\b|\bspec|manual|документац|даташит|скачать|описани"
                   r"|数据手册|规格书|下载|资料|手册", re.I)


class _Links(HTMLParser):
    """Собирает (тег, адрес, type, текст ссылки) и `<base href>`; терпит оборванную разметку."""

    def __init__(self) -> None:
        HTMLParser.__init__(self, convert_charrefs=True)
        self.items: List[List[str]] = []
        self.base = ""
        self.title = ""
        self._open: Optional[List[str]] = None       # незакрытая <a>: в неё дописывается текст
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "").strip() for k, v in attrs}
        if tag == "base" and a.get("href") and not self.base:
            self.base = a["href"]
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._open = None
            if a.get("href"):
                self._open = ["a", a["href"], a.get("type", ""), ""]
                self.items.append(self._open)
        elif tag in _FRAME_ATTR and a.get(_FRAME_ATTR[tag]):
            self.items.append([tag, a[_FRAME_ATTR[tag]], a.get("type", ""), a.get("title", "")])

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._open = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._open is not None:
            self._open[3] += data


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _pdf_target(url: str, type_attr: str) -> str:
    """Адрес PDF, если ссылка ведёт на PDF, иначе пусто. У просмотрщика — адрес файла из параметра."""
    parts = urlsplit(url)
    if parts.path.lower().endswith(".pdf"):
        return url
    for _name, value in parse_qsl(parts.query):
        if urlsplit(value).path.lower().endswith(".pdf"):
            target = urldefrag(urljoin(url, value))[0]
            if urlsplit(target).scheme in ("http", "https"):
                return target
    return url if "application/pdf" in type_attr.lower() else ""


def page_links(html: str, base: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """([(адрес PDF, текст ссылки)], [вложенные страницы, которые стоит открыть]) — без повторов, по порядку."""
    parser = _Links()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as e:       # noqa: BLE001 — чужая разметка; что успели собрать, то и берём
        log.info("разбор %s: %s", base, e)
    base = urljoin(base, parser.base) if parser.base else base
    pdfs: List[Tuple[str, str]] = []
    pages: List[str] = []
    seen = set()
    for tag, raw, type_attr, text in parser.items:
        url = urldefrag(urljoin(base, raw))[0]
        if urlsplit(url).scheme not in ("http", "https") or not host_of(url):
            continue
        target = _pdf_target(url, type_attr)
        if target:
            if target not in seen:
                seen.add(target)
                pdfs.append((target, _clean(text)))
        elif url not in seen and (tag != "a" or _HINT.search(text) or _HINT.search(urlsplit(url).path)):
            seen.add(url)
            pages.append(url)
    return pdfs, pages


def _blocked_site(exc: Exception, url: str) -> str:
    """Домен из сообщения белого списка (после редиректа он не совпадает с адресом страницы)."""
    m = re.search(r"списке: (\S+)", str(exc))
    return m.group(1) if m else host_of(url)


def crawl(http: Any, lead: Lead, bus: Optional[EventBus] = None, max_depth: int = MAX_DEPTH,
          max_pages: int = MAX_PAGES, cancelled: Callable[[], bool] = lambda: False,
          force: bool = False, tracker: Optional[BlockTracker] = None) -> List[Lead]:
    """Ссылки на PDF со страницы `lead.url` и вложенных страниц. `snippet` найденной ссылки — адрес страницы,
    где она стояла (нужен как Referer при скачивании); источник, уровень, запрос и язык — от `lead`.
    `force` — открыть как страницу, даже если адрес похож на PDF (сервер вместо файла отдал HTML).
    `tracker` — счёт сетевых неудач по доменам (§4.11); без него разовая неудача сети — `transient`."""
    if not force and (lead.kind == "pdf" or urlsplit(lead.url).path.lower().endswith(".pdf")):
        return [lead]
    if cancelled():
        return []
    site = host_of(lead.url)
    lang = lead.language or search_language(query=lead.query, domain=site)
    kw = dict(lang=lang, level=lead.level, source=lead.source_id or site)
    emit = bus.emit if bus is not None else (lambda *a, **k: None)

    found: List[Lead] = []
    seen_pdf = set()
    visited = {normalize_url(lead.url)}
    queue: List[Tuple[str, int, str]] = [(lead.url, 1, "")]       # (адрес, глубина, откуда пришли)
    opened = 0
    while queue and opened < max(1, max_pages) and not cancelled():
        url, depth, referer = queue.pop(0)
        opened += 1
        try:
            final, html = http.get_html(url, referer=referer)
        except Exception as e:       # noqa: BLE001 — сеть может бросить что угодно
            log.info("обход %s: %s", url, e)
            if depth == 1:           # стартовая страница недоступна — причина идёт в ход поиска
                cls = failure_class(e, tracker, site)
                if cls:
                    emit("access." + cls, site=_blocked_site(e, url) if cls == "not_whitelisted" else site, **kw)
                else:
                    emit("crawl.empty", site=site, **kw)
                return []
            continue
        visited.add(normalize_url(final))
        if tracker is not None:
            tracker.ok(host_of(final))
        if html.lstrip()[:5] == "%PDF-":       # «страница» оказалась самим документом
            pdfs, pages = [(final, lead.title)], []
        else:
            pdfs, pages = page_links(html, final)
        for pdf, text in pdfs:
            key = normalize_url(pdf)
            if key not in seen_pdf:
                seen_pdf.add(key)
                found.append(replace(lead, url=pdf, title=text or lead.title, kind="pdf", snippet=final,
                                     rank_score=0.0))
        if depth >= max_depth:
            continue
        for page in pages:
            key = normalize_url(page)
            if key not in visited and http.is_allowed(page):
                visited.add(key)
                queue.append((page, depth + 1, final))
    if cancelled():
        return []
    found.sort(key=lambda x: not http.is_allowed(x.url))       # устойчиво: свои домены вперёд
    emit("crawl.found" if found else "crawl.empty", site=site, n=len(found), **kw)
    return found
