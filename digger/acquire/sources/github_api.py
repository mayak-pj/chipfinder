# -*- coding: utf-8 -*-
"""GitHub Search API: репозитории с документацией (ARCHITECTURE §4.2, шаг 2.14).

Без ключа: 10 запросов в минуту. Лимит (403/429 с `rate limit`) — событие `engine.quota`.
Результат — страницы репозиториев (`kind="page"`): PDF оттуда достаёт обход страниц (шаг 3.3).
"""
from __future__ import annotations

import logging
from typing import Any, List
from urllib.parse import quote_plus

from ..models import Lead
from ..registry import register
from .base import SourceAdapter, SourceError

log = logging.getLogger("digger.acquire.github_api")
DEFAULT_URL = "https://api.github.com/search/repositories?q={part}+in:name,description,readme&per_page=10"
_QUOTA = ("HTTP 429", "HTTP 403")


@register
class GithubApi(SourceAdapter):
    adapter = "github_api"
    family = "site"
    kinds = ("page",)

    def find(self, query: Any, http: Any) -> List[Lead]:
        text = getattr(query, "text", query) or ""
        if not text.strip():
            return []
        url = (self.entry.options.get("url") or DEFAULT_URL).format(part=quote_plus(text), q=quote_plus(text))
        try:
            data = http.get_json(url)
        except Exception as e:
            detail = str(e) or type(e).__name__
            log.warning("GitHub API: %s", detail)
            raise SourceError("engine.quota" if detail in _QUOTA else "engine.error", detail)
        leads = []
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            link = item.get("html_url") or ""
            if not link:
                continue
            title = item.get("full_name") or item.get("name") or link
            leads.append(Lead(url=link, title=title, snippet=(item.get("description") or "").strip(), kind="page"))
        return leads
