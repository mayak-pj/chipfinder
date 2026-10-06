# -*- coding: utf-8 -*-
"""Скачивание в карантин (ARCHITECTURE §4.2, шаг 3.1).

Сеть — только через `SafeHttp.download_pdf` (белый список, редиректы, лимит размера, сигнатура %PDF-).
Здесь — повторы и итог: 5xx и сбой соединения повторяются с паузой, 4xx / не PDF / превышение размера — нет.
Проверка содержимого (шифрование, активное содержимое) — следующий шаг, `acquire/validate.py`.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlsplit

from ..core.netsafe import HttpStatus, NetBlocked, host_of
from .events import EventBus
from .models import FetchResult, Lead
from .netdiag import BlockTracker, failure_class

log = logging.getLogger("chipfinder.acquire.fetch")
ATTEMPTS = 3
PAUSE_SEC = 5.0


def _file_name(url: str) -> str:
    name = os.path.basename(unquote(urlsplit(url).path)) or host_of(url)
    return name[:80]


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, HttpStatus):
        return exc.status >= 500
    return not isinstance(exc, NetBlocked)      # NetBlocked: правила и лимиты, повтор бесполезен


def fetch_to_quarantine(http: Any, lead: Lead, bus: Optional[EventBus] = None, referer: str = "",
                        attempts: int = ATTEMPTS, pause: float = PAUSE_SEC,
                        sleep: Callable[[float], None] = time.sleep,
                        tracker: Optional[BlockTracker] = None) -> FetchResult:
    """Скачивает `lead.url` в карантин. Не бросает исключений: итог в `FetchResult`.
    `tracker` — счёт сетевых неудач по доменам (§4.11); без него разовая неудача сети — `transient`."""
    emit = bus.emit if bus is not None else (lambda *a, **k: None)
    site = host_of(lead.url)
    name = _file_name(lead.url)
    kw = dict(lang=lead.language or "en", level=lead.level, source=lead.source_id or site)
    emit("fetch.start", file=name, site=site, **kw)
    error: Exception = NetBlocked("не выполнялось")
    for attempt in range(1, max(1, attempts) + 1):
        try:
            path, sha, size, _danger, final = http.download_pdf_ex(lead.url, referer=referer)
        except Exception as e:       # noqa: BLE001 — сеть может бросить что угодно
            error = e
            log.info("скачивание %s, попытка %d: %s", lead.url, attempt, e)
            if _retryable(e) and attempt < attempts:
                sleep(pause)
                continue
            break
        if tracker is not None:
            tracker.ok(site)
        emit("fetch.done", file=name, size=size, **kw)
        emit("quarantine.placed", **kw)
        return FetchResult(ok=True, path_in_quarantine=path, sha256=sha, size=size,
                           content_type="application/pdf", final_url=final)
    emit("fetch.failed", file=name, site=site, **kw)
    text = str(error) or type(error).__name__
    return FetchResult(ok=False, error=text, final_url=lead.url, failure_class=failure_class(error, tracker, site))
