# -*- coding: utf-8 -*-
"""AllDatasheet (шаг 2.8): поиск → страницы деталей → PDF.

Выдача `view.jsp?Searchword=<part>` содержит ссылки на страницы деталей `/datasheet-pdf/view/<id>/<MAKER>/<PART>.html`.
Со страницы детали берутся ссылки на PDF (`.pdf`, `/datasheet-pdf/pdf/…`, iframe/embed/object); если их нет,
PDF-адрес строится из адреса детали (`view` → `pdf`). Глубина — одна страница детали (`follow`), не больше
`MAX_DETAILS` деталей за запрос. Универсальный обход страниц — `acquire/crawl.py` (шаг 3.3); пока обход здесь.
Сайт закрыт Cloudflare («Just a moment…»): это капча — капчу не обходим, домен «отдыхает» `REST_MINUTES` минут.
Вёрстка записана по описанию, а не с живой страницы (с Mac сайт отдаёт проверку Cloudflare): сверить на выезде 2.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
import time
from typing import Any, Callable, List, Tuple
from urllib.parse import quote, urljoin, urlsplit

from ...models import Lead
from ...query import base_part
from ...registry import register
from ..base import SourceAdapter, SourceError
from ..makers import part_of

log = logging.getLogger("chipfinder.acquire.alldatasheet")
REST_MINUTES = 15
MAX_DETAILS = 5
_A = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.S | re.I)
_SRC = re.compile(r"<(?:iframe|embed|object)\b[^>]*?\b(?:src|data)=\"([^\"]+)\"", re.S | re.I)
_ATTR = r"\b%s=\"([^\"]*)\""
_TAG = re.compile(r"<[^>]+>")
_DETAIL = re.compile(r"/datasheet-pdf/view/\d+/[^/?#]+/[^/?#]+\.html", re.I)
_PDF_PATH = re.compile(r"(\.pdf$|/datasheet-pdf/pdf/\d+/)", re.I)


def is_blocked(status: int, page: str) -> bool:
    """Страница проверки Cloudflare вместо сайта."""
    low = page[:200000].lower()
    return ("just a moment" in low and "challenge" in low) or "challenges.cloudflare.com" in low or (
        status in (403, 503) and "cloudflare" in low)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(_TAG.sub("", fragment))).strip()


def _href(attrs: str) -> str:
    m = re.search(_ATTR % "href", attrs, re.I)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def detail_links(page: str, base: str) -> List[Tuple[str, str]]:
    """[(адрес детали, заголовок)] из выдачи, без повторов, в порядке выдачи."""
    out, seen = [], set()
    for m in _A.finditer(page):
        url = urljoin(base, _href(m.group(1)))
        path = urlsplit(url).path
        if not _DETAIL.search(path) or urlsplit(url).scheme not in ("http", "https"):
            continue
        key = url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append((key, _text(m.group(2)) or path.rsplit("/", 1)[-1][:-5]))
    return out


def pdf_links(page: str, base: str) -> List[str]:
    """Адреса PDF со страницы детали: a/iframe/embed/object; повторы убраны."""
    found = [_href(m.group(1)) for m in _A.finditer(page)] + [htmllib.unescape(m) for m in _SRC.findall(page)]
    out = []
    for raw in found:
        url = urljoin(base, raw)
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and _PDF_PATH.search(parts.path) and url not in out:
            out.append(url)
    return out


def pdf_from_detail(url: str) -> str:
    """`…/datasheet-pdf/view/<id>/<MAKER>/<PART>.html` → `…/datasheet-pdf/pdf/<id>/<MAKER>/<PART>.html`."""
    return url.replace("/datasheet-pdf/view/", "/datasheet-pdf/pdf/", 1)


@register
class AllDatasheet(SourceAdapter):
    adapter = "alldatasheet"
    family = "site"
    kinds = ("pdf", "page")

    def __init__(self, *args: Any, clock: Callable[[], float] = time.time, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._rest_until = 0.0

    def _get(self, http: Any, url: str) -> Tuple[str, str]:
        """(адрес после переадресаций, текст страницы); капча — SourceError, сбой сети — тоже."""
        try:
            reply = http.fetch(url)
        except Exception as e:
            detail = str(e) or type(e).__name__
            log.warning("%s: %s", self.id, detail)
            raise SourceError("engine.error", detail)
        page = reply["body"].decode("utf-8", errors="replace")
        if is_blocked(reply["status"], page):
            self._rest_until = self._clock() + REST_MINUTES * 60
            raise SourceError("engine.captcha", "Cloudflare", minutes=REST_MINUTES)
        if reply["status"] >= 400:
            raise SourceError("engine.quota" if reply["status"] == 429 else "engine.error", "HTTP %d" % reply["status"])
        return reply["url"], page

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        if not part:
            return []
        left = self._rest_until - self._clock()
        if left > 0:
            raise SourceError("engine.captcha", "отдых", minutes=int(-(-left // 60)))
        base, page = self._get(http, self.entry.options["url"].format(part=quote(part)))
        wanted = {part.upper(), (base_part(part) or part).upper()}
        details = detail_links(page, base)
        details.sort(key=lambda d: not any(w in d[0].upper() for w in wanted))      # свои партномера — вперёд
        leads: List[Lead] = []
        seen = set()
        follow = int(self.entry.options.get("follow", 1))
        for n, (url, title) in enumerate(details[:MAX_DETAILS]):
            pdfs: List[str] = []
            if follow:
                try:
                    dbase, dpage = self._get(http, url)
                    pdfs = pdf_links(dpage, dbase)
                except SourceError as e:
                    if e.key == "engine.captcha":
                        raise
                    log.info("%s: деталь %s — %s", self.id, url, e.detail)
            for pdf in pdfs or ([pdf_from_detail(url)] if follow else []):
                if pdf not in seen:
                    seen.add(pdf)
                    leads.append(Lead(url=pdf, title=title, kind="pdf", snippet=url))
            if url not in seen:
                seen.add(url)
                leads.append(Lead(url=url, title=title, kind="page"))
        return leads
