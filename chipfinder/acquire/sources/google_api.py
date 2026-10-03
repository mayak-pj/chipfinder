# -*- coding: utf-8 -*-
"""Google Custom Search JSON API (ARCHITECTURE §4.2, шаг 2.1).

Включается только при ключе: `config.json → acquire.api_keys.google_api = {"key": …, "cx": …}`.
Без ключа `search()` публикует «нет ключа, пропуск» и в сеть не ходит. Лимиты и условия — «Решения».
"""
from __future__ import annotations

import logging
from typing import Any, List
from urllib.parse import quote_plus, urlsplit

from ..models import Lead
from ..registry import register
from .base import SourceAdapter, SourceError

log = logging.getLogger("chipfinder.acquire.google_api")
DEFAULT_URL = "https://www.googleapis.com/customsearch/v1?key={key}&cx={cx}&q={q}"
_QUOTA = ("HTTP 429", "HTTP 403")        # 429 — лимит запросов, 403 — суточная квота исчерпана или ключ отключён


@register
class GoogleApi(SourceAdapter):
    adapter = "google_api"
    family = "engine"
    kinds = ("pdf", "page")

    def find(self, query: Any, http: Any) -> List[Lead]:
        text = getattr(query, "text", query) or ""
        key = self.key or {}
        url = (self.entry.options.get("url") or DEFAULT_URL).format(
            key=quote_plus(str(key.get("key", ""))), cx=quote_plus(str(key.get("cx", ""))), q=quote_plus(text))
        try:
            data = http.get_json(url)
        except Exception as e:      # сеть, код ≥ 400, не JSON: ключ в сообщение не попадает
            detail = str(e) or type(e).__name__
            log.warning("Google API: %s", detail)
            raise SourceError("engine.quota" if detail in _QUOTA else "engine.error", detail)
        leads = []
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            link = item.get("link") or ""
            if not link:
                continue
            is_pdf = item.get("mime") == "application/pdf" or urlsplit(link).path.lower().endswith(".pdf")
            leads.append(Lead(url=link, title=item.get("title", ""), snippet=(item.get("snippet") or "").strip(),
                              kind="pdf" if is_pdf else "page"))
        return leads
