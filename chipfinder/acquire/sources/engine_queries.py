# -*- coding: utf-8 -*-
"""Запросы-шаблоны через поисковики (ARCHITECTURE §4.2, шаг 2.15a).

Источник из `sources.json` со списком `queries` (шаблоны `{part}`, `{code}`, `{maker_site}`) и списком поисковиков `via`.
Для каждого шаблона поисковики пробуются по порядку, пока один не вернёт ссылки; остальные не трогаются.
Ход поиска — события самих поисковиков (`engine.*`, со своей капчей и отдыхом); свои события — только если
подходящих шаблонов нет. Лиды получают `source_id` и `level` этого источника.
"""
from __future__ import annotations

import logging
from typing import Any, List

from ..models import Lead
from ..registry import register
from .base import SourceAdapter

log = logging.getLogger("chipfinder.acquire.engine_queries")


def applicable(templates: List[str], kind: str, maker_site: str = "") -> List[str]:
    """Шаблоны под вид запроса: smd → с `{code}`; part/family → с `{part}`; без вида — все."""
    out = []
    for t in templates:
        if "{maker_site}" in t and not maker_site:
            continue
        if kind == "smd" and "{code}" not in t:
            continue
        if kind in ("part", "family") and "{part}" not in t:
            continue
        out.append(t)
    return out


@register
class EngineQueries(SourceAdapter):
    adapter = "engine_queries"
    family = "queries"
    kinds = ("pdf", "page")
    registry: Any = None            # Registry.build() проставляет сам: отсюда берутся поисковики из `via`

    _asked = 0

    def find(self, query: Any, http: Any) -> List[Lead]:
        text = (getattr(query, "text", query) or "").strip()
        kind = getattr(query, "kind", "")
        maker_site = getattr(query, "maker_site", "")
        lang = getattr(query, "lang", "")
        engines = [self.registry.engine(i) for i in self.entry.options.get("via", [])] if self.registry else []
        engines = [e for e in engines if e is not None]
        leads: List[Lead] = []
        seen = set()
        if text and engines:
            from ..query import Query
            for tpl in applicable(self.entry.queries, kind, maker_site):
                q = tpl.format(part=text, code=text, maker_site=maker_site)
                for engine in engines:
                    self._asked += 1
                    found = engine.search(Query(self.entry.lang or lang, q, kind or "part"), http)
                    for lead in found:
                        if lead.url not in seen:
                            seen.add(lead.url)
                            lead.source_id, lead.level = self.id, self.level
                            leads.append(lead)
                    if found:
                        break
        return leads

    def search(self, query: Any, http: Any) -> List[Lead]:
        self._asked = 0
        leads = self.find(query, http)
        if not self._asked:           # ни один поисковик не спрашивали: нет шаблонов, поисковиков или пустой запрос
            text = getattr(query, "text", query) or ""
            self._emit("site.empty", getattr(query, "lang", "") or "en", {"site": self.label, "query": text, "n": 0, "pdfs": 0})
        return leads
