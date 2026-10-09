# -*- coding: utf-8 -*-
"""Каталоги-подсказки без прямых PDF (шаг 2.9): Datasheet Archive и FindChips.

Оба сайта — витрины дистрибьюторов (Supplyframe): в выдаче нет ссылок на PDF, только партномера, производители и
переходы к магазинам. Поэтому они дают страницы-кандидаты (`kind="page"`), а из заголовка берутся факты
«партномер — производитель», по которым строятся адреса сайта производителя (`maker_url`) и запросы.
 • Datasheet Archive: ссылки строк выдачи — трекер `analytics.supplyframe.com` (чужой домен, не в белом списке),
   поэтому лид указывает на страницу поиска по этому партномеру `/?q=<part>`.
 • FindChips: `<a class="pdp-link-*" href="/detail/<part>/<maker>">`; страницы деталей закрыты Cloudflare —
   это капча (домен отдыхает), она не мешает самой выдаче.
Вёрстка записана с живых страниц 2026-10-04.
"""
from __future__ import annotations

import html as htmllib
import re
from typing import Any, List
from urllib.parse import quote, unquote, urljoin, urlsplit

from ...models import Lead
from ...query import base_part
from ...registry import register
from ..makers import part_of
from .common import _A, CatalogSite, href_of, text_of

MAX_LEADS = 10
_DSA_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.S | re.I)
_DSA_MFR = re.compile(r'class="mfr-label[^"]*"[^>]*>(.*?)</span', re.S | re.I)
_DSA_DESC = re.compile(r'<td\b[^>]*help-class-3[^>]*>(.*?)</td', re.S | re.I)
_FC_DETAIL = re.compile(r"^/detail/([^/?#]+)/([^/?#]+)$", re.I)


def _first(wanted: Any, items: List[Any], key: Any) -> List[Any]:
    return sorted(items, key=lambda x: not any(w in key(x).upper() for w in wanted))


def dsa_rows(page: str) -> List[Any]:
    """[(партномер, производитель, описание)] из таблицы «Result Highlights», без повторов."""
    out, seen = [], set()
    for row in _DSA_ROW.finditer(page):
        a = _A.search(row.group(1))
        mfr = _DSA_MFR.search(row.group(1))
        if not a or not mfr or "help-class-1" not in row.group(1):
            continue
        part = text_of(a.group(2))
        if not part or part.upper() in seen:
            continue
        seen.add(part.upper())
        desc = _DSA_DESC.search(row.group(1))
        out.append((part, text_of(mfr.group(1)), text_of(desc.group(1)) if desc else ""))
    return out


def fc_details(page: str, base: str) -> List[Any]:
    """[(адрес детали, партномер, производитель)] из ссылок `/detail/<part>/<maker>`, без повторов."""
    out, seen = [], set()
    for m in _A.finditer(page):
        url = urljoin(base, href_of(m.group(1))).split("#")[0]
        found = _FC_DETAIL.match(urlsplit(url).path)
        if not found or url in seen or urlsplit(url).scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append((url, unquote(found.group(1)), unquote(found.group(2)).replace("-", " ")))
    return out


@register
class DatasheetArchive(CatalogSite):
    adapter = "datasheetarchive"
    kinds = ("page",)

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        if not part:
            return []
        self.check_rest()
        base, page = self._get(http, self.entry.options["url"].format(part=quote(part)))
        wanted = {part.upper(), (base_part(part) or part).upper()}
        leads = []
        for name, maker, desc in _first(wanted, dsa_rows(page), lambda r: r[0])[:MAX_LEADS]:
            title = " — ".join(x for x in (name, maker, desc) if x)
            leads.append(Lead(url=urljoin(base, "/?q=" + quote(name)), title=title, kind="page"))
        return leads


@register
class FindChips(CatalogSite):
    adapter = "findchips"
    kinds = ("page",)

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        if not part:
            return []
        self.check_rest()
        base, page = self._get(http, self.entry.options["url"].format(part=quote(part)))
        wanted = {part.upper(), (base_part(part) or part).upper()}
        found = _first(wanted, fc_details(page, base), lambda d: d[1])[:MAX_LEADS]
        return [Lead(url=url, title=htmllib.unescape("%s — %s" % (name, maker)), kind="page")
                for url, name, maker in found]
