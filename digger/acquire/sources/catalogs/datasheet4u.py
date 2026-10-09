# -*- coding: utf-8 -*-
"""Datasheet4U (шаг 2.9): поиск → деталь → страница загрузки → PDF.

Поиск: `/share_search.php?sWord=<part>` (старый `search.php?q=` отдаёт главную). В выдаче строки
`<производитель, партномер, описание>`, партномер — ссылка `/datasheets/<производитель>/<партномер>/<id>`.
Страница загрузки `/download/<id>/<партномер>.html` содержит `<a>`/`<iframe>` на `/pdf/<id>/<партномер>.pdf`.
Страницу детали не открываем: id и партномер есть в её адресе. Если страница загрузки недоступна (не капча),
PDF-адрес строится из тех же id и партномера. Вёрстка записана с живого сайта 2026-10-04.
Тот же адаптер обслуживает сестринский сайт datasheetspdf.com (в `data/sources.json` выключен: с Mac не открывается,
шаблон не проверен — включить после выезда 2).
"""
from __future__ import annotations

import logging
import re
from typing import Any, List
from urllib.parse import quote, urljoin, urlsplit

from ...models import Lead
from ...query import base_part
from ...registry import register
from ..base import SourceError
from ..makers import part_of
from .common import _A, CatalogSite, href_of, pdf_urls, text_of

log = logging.getLogger("digger.acquire.datasheet4u")
MAX_DETAILS = 5
_DETAIL = re.compile(r"^/datasheets/[^/?#]+/([^/?#]+)/(\d+)/?$", re.I)
_PDF_PATH = re.compile(r"^/pdf/\d+/[^/?#]+\.pdf$", re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.S | re.I)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td\s*>", re.S | re.I)


class Hit:
    """Строка выдачи."""

    def __init__(self, url: str, part: str, maker: str, desc: str, doc_id: str):
        self.url, self.part, self.maker, self.desc, self.doc_id = url, part, maker, desc, doc_id

    @property
    def title(self) -> str:
        return " — ".join(x for x in (self.part, self.maker, self.desc) if x)


def hits(page: str, base: str) -> List[Hit]:
    """Строки выдачи без повторов, в порядке сайта."""
    out: List[Hit] = []
    seen = set()
    for row in _ROW.finditer(page):
        cells = _CELL.findall(row.group(1))
        for m in _A.finditer(row.group(1)):
            url = urljoin(base, href_of(m.group(1)))
            found = _DETAIL.match(urlsplit(url).path)
            if not found or urlsplit(url).scheme not in ("http", "https") or url in seen:
                continue
            seen.add(url)
            maker = text_of(cells[0]) if len(cells) > 2 else ""
            desc = text_of(cells[2]) if len(cells) > 2 else ""
            out.append(Hit(url, text_of(m.group(2)) or found.group(1), maker, desc, found.group(2)))
    return out


def download_url(hit: Hit) -> str:
    return urljoin(hit.url, "/download/%s/%s.html" % (hit.doc_id, quote(hit.part)))


def pdf_from_hit(hit: Hit) -> str:
    return urljoin(hit.url, "/pdf/%s/%s.pdf" % (hit.doc_id, quote(hit.part)))


@register
class Datasheet4u(CatalogSite):
    adapter = "datasheet4u"

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        if not part:
            return []
        self.check_rest()
        base, page = self._get(http, self.entry.options["url"].format(part=quote(part)))
        wanted = {part.upper(), (base_part(part) or part).upper()}
        found = hits(page, base)
        found.sort(key=lambda h: not any(w in h.part.upper() for w in wanted))      # свои партномера — вперёд
        follow = int(self.entry.options.get("follow", 1))
        leads: List[Lead] = []
        seen = set()
        for hit in found[:MAX_DETAILS]:
            pdfs: List[str] = []
            if follow:
                try:
                    dbase, dpage = self._get(http, download_url(hit))
                    pdfs = pdf_urls(dpage, dbase, _PDF_PATH)
                except SourceError as e:
                    if e.key == "engine.captcha":
                        raise
                    log.info("%s: загрузка %s — %s", self.id, hit.url, e.detail)
            for pdf in pdfs or ([pdf_from_hit(hit)] if follow else []):
                if pdf not in seen:
                    seen.add(pdf)
                    leads.append(Lead(url=pdf, title=hit.title, kind="pdf", snippet=hit.url))
            seen.add(hit.url)
            leads.append(Lead(url=hit.url, title=hit.title, kind="page"))
        return leads
