# -*- coding: utf-8 -*-
"""Прямые адреса datasheet на сайтах производителей (ARCHITECTURE §4.2, шаг 2.7).

Источник `maker_url` из `data/sources.json`: `prefixes` — регулярки начала партномера (по ним решается, чей это
чип; не подошёл — в сеть не ходим и события не пишем), `urls` — шаблоны адресов с `{part}` и `{part_lower}`.
Адрес принимается, только если ответ 200 и первые байты — `%PDF` (у шаблонов с `"kind": "page"` в `page_urls` —
страница HTML). Поиск `site:` по сайту производителя — отдельный источник `maker_queries` (поисковики).
"""
from __future__ import annotations

import logging
import re
from typing import Any, List

from ...models import Lead
from ...query import base_part
from ...registry import register
from ..base import SourceAdapter, SourceError

log = logging.getLogger("chipfinder.acquire.makers")
MAX_URLS = 8          # потолок обращений к одному сайту за запрос


def part_of(query: Any) -> str:
    """Партномер из запроса: первое слово («STM32F103C8 datasheet pdf» → «STM32F103C8»)."""
    words = (getattr(query, "text", query) or "").split()
    return words[0].upper() if words else ""


@register
class MakerUrl(SourceAdapter):
    adapter = "maker_url"
    family = "maker"
    kinds = ("pdf", "page")

    def applies(self, part: str) -> bool:
        return bool(part) and any(re.match(p, part, re.I) for p in self.entry.options.get("prefixes", []))

    def search(self, query: Any, http: Any) -> List[Lead]:
        if not self.applies(part_of(query)):
            return []
        return super().search(query, http)

    def find(self, query: Any, http: Any) -> List[Lead]:
        part = part_of(query)
        names = list(dict.fromkeys([part, base_part(part) or part]))     # с суффиксом корпуса и без него
        options = self.entry.options
        todo = [(u, "pdf") for u in options.get("urls", [])] + [(u, "page") for u in options.get("page_urls", [])]
        leads: List[Lead] = []
        seen = set()
        asked = failed = 0
        for template, kind in todo:
            for name in names:
                url = template.format(part=name, part_lower=name.lower())
                if url in seen or asked >= MAX_URLS:
                    continue
                seen.add(url)
                asked += 1
                try:
                    reply = http.fetch(url, max_bytes=2048)
                except Exception as e:      # сеть, домен вне белого списка: пробуем остальные адреса
                    failed += 1
                    log.info("%s: %s — %s", self.id, url, e or type(e).__name__)
                    continue
                if reply["status"] != 200 or not _looks_like(kind, reply["body"]):
                    continue
                leads.append(Lead(url=reply["url"], title="%s — %s" % (part, self.label), kind=kind))
        if asked and failed == asked:
            raise SourceError("engine.error", "сайт недоступен")
        return leads


def _looks_like(kind: str, body: bytes) -> bool:
    head = (body or b"")[:1024].lstrip()
    if kind == "pdf":
        return head.startswith(b"%PDF")
    return head[:15].lower().startswith((b"<!doctype html", b"<html"))
