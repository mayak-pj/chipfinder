# -*- coding: utf-8 -*-
"""AllDatasheet (шаг 2.8): поиск → страницы деталей → PDF.

Выдача `view.jsp?Searchword=<part>` содержит ссылки на страницы деталей `/datasheet-pdf/view/<id>/<MAKER>/<PART>.html`.
Со страницы детали берутся ссылки на PDF (`.pdf`, `/datasheet-pdf/pdf/…`, iframe/embed/object); если их нет,
PDF-адрес строится из адреса детали (`view` → `pdf`). Глубина — одна страница детали (`follow`), не больше
`MAX_DETAILS` деталей за запрос. Универсальный обход чужих страниц — `acquire/crawl.py`; здесь — свой, по известной вёрстке сайта.
Сайт закрыт Cloudflare («Just a moment…»): это капча — капчу не обходим, домен «отдыхает» `REST_MINUTES` минут.
Вёрстка записана по описанию, а не с живой страницы (с Mac сайт отдаёт проверку Cloudflare): сверить на выезде 2.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Tuple
from urllib.parse import quote, urljoin, urlsplit

from ...models import Lead
from ...query import base_part
from ...registry import register
from ..base import SourceError
from ..makers import part_of
from .common import _A, CatalogSite, href_of, is_blocked, pdf_urls, text_of  # noqa: F401  (is_blocked — для тестов)

log = logging.getLogger("digger.acquire.alldatasheet")
MAX_DETAILS = 5
_DETAIL = re.compile(r"/datasheet-pdf/view/\d+/[^/?#]+/[^/?#]+\.html", re.I)
_PDF_PATH = re.compile(r"(\.pdf$|/datasheet-pdf/pdf/\d+/)", re.I)


def detail_links(page: str, base: str) -> List[Tuple[str, str]]:
    """[(адрес детали, заголовок)] из выдачи, без повторов, в порядке выдачи."""
    out, seen = [], set()
    for m in _A.finditer(page):
        url = urljoin(base, href_of(m.group(1)))
        path = urlsplit(url).path
        if not _DETAIL.search(path) or urlsplit(url).scheme not in ("http", "https"):
            continue
        key = url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append((key, text_of(m.group(2)) or path.rsplit("/", 1)[-1][:-5]))
    return out


def pdf_links(page: str, base: str) -> List[str]:
    """Адреса PDF со страницы детали: a/iframe/embed/object; повторы убраны."""
    return pdf_urls(page, base, _PDF_PATH)


def pdf_from_detail(url: str) -> str:
    """`…/datasheet-pdf/view/<id>/<MAKER>/<PART>.html` → `…/datasheet-pdf/pdf/<id>/<MAKER>/<PART>.html`."""
    return url.replace("/datasheet-pdf/view/", "/datasheet-pdf/pdf/", 1)


@register
class AllDatasheet(CatalogSite):
    adapter = "alldatasheet"

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        if not part:
            return []
        self.check_rest()
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
