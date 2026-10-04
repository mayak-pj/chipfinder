# -*- coding: utf-8 -*-
"""Диагностика по адаптерам (ARCHITECTURE §4.11, шаг 2.15).

Каждый включённый источник из `data/sources.json` получает тестовый запрос (по умолчанию NE555), итог — один из:
ok (есть результат) · empty (пусто) · captcha · no_key · quota (лимит) · error (сеть, код ≥ 400) ·
parse_error (адаптер упал на ответе — вёрстка изменилась) · no_adapter (в sources.json есть, класса нет) ·
disabled (выключен, не проверялся). Вердикт берётся из событий хода поиска, а не из текста.
Если передан `record_dir`, сырые ответы сайта (урезанные) сохраняются — из них делают фикстуры (шаг 3.4).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .events import EventBus, render
from .query import Query
from .registry import ADAPTERS, Registry, load_builtin

log = logging.getLogger("chipfinder.acquire.diagnose")
TEST_PART = "NE555"
SAVE_BYTES = 150 * 1024
STATUSES = ("ok", "empty", "captcha", "no_key", "quota", "error", "parse_error", "no_adapter", "disabled")
BAD = ("captcha", "quota", "error", "parse_error", "no_adapter")
_BY_EVENT = {"engine.no_key": "no_key", "engine.captcha": "captcha", "engine.quota": "quota", "engine.error": "error"}


class Recorder:
    """Обёртка над SafeHttp: запоминает ответы `fetch` и `get_json` последнего вызова адаптера."""

    def __init__(self, http: Any):
        self._http = http
        self.saved: List[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._http, name)

    def fetch(self, url: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        r = self._http.fetch(url, *args, **kwargs)
        self.saved.append((url, r.get("status"), bytes(r.get("body") or b"")[:SAVE_BYTES]))
        return r

    def get_json(self, url: str) -> Any:
        data = self._http.get_json(url)
        self.saved.append((url, 200, json.dumps(data, ensure_ascii=False).encode("utf-8")[:SAVE_BYTES]))
        return data


def _status_of(events: List[Any]) -> str:
    for e in reversed(events):
        if e.key in _BY_EVENT:
            return _BY_EVENT[e.key]
        if e.key.endswith(".empty"):
            return "empty"
        if e.key.endswith(".found") or e.key.endswith(".found_pdf"):
            return "ok"
    return "error"            # события не пришли: адаптер ничего не сообщил


def _safe(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text, flags=re.U)[:60]


def diagnose_adapters(sources_path: Optional[str], http: Any, keys: Optional[Dict[str, Any]] = None,
                      part: str = TEST_PART, progress: Optional[Callable[[str], None]] = None,
                      cancel: Any = None, record_dir: Optional[str] = None,
                      data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Строки по источникам: id, name, adapter, level, status, detail, leads, pdfs, seconds, domains, saved."""
    say = progress or (lambda m: None)
    load_builtin()
    bus = EventBus()
    events: List[Any] = []
    bus.subscribe(events.append)
    reg = Registry(data, keys=keys, bus=bus) if data is not None else Registry.load(sources_path, keys=keys, bus=bus)
    entries = reg.entries(include_disabled=True)
    rows: List[Dict[str, Any]] = []
    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
    for i, entry in enumerate(entries):
        if cancel is not None and getattr(cancel, "cancelled", False):
            break
        row = {"id": entry.id, "name": entry.name or entry.id, "adapter": entry.adapter, "level": entry.level,
               "domains": list(entry.domains), "status": "", "detail": "", "leads": 0, "pdfs": 0, "seconds": 0.0,
               "saved": []}
        rows.append(row)
        cls = ADAPTERS.get(entry.adapter)
        if not entry.enabled:
            row.update(status="disabled", detail="выключен в sources.json")
            continue
        if cls is None:
            row.update(status="no_adapter", detail="нет адаптера «%s»" % entry.adapter)
            continue
        say("Проверка %d/%d: %s" % (i + 1, len(entries), row["name"]))
        ad = cls(entry, bus=bus, key=reg.keys.get(entry.needs_key) if entry.needs_key else None)
        rec = Recorder(http)
        text = part + " datasheet pdf" if ad.family == "engine" else part
        del events[:]
        t0 = time.time()
        try:
            leads = ad.search(Query("en", text, "part"), rec)
            row["status"] = _status_of(events)
        except Exception as e:  # noqa — адаптер не должен падать; если упал, вёрстка не разобрана
            log.warning("диагностика: %s упал: %s", entry.id, e)
            leads = []
            row.update(status="parse_error", detail="%s: %s" % (type(e).__name__, e))
        row["seconds"] = round(time.time() - t0, 2)
        row["leads"] = len(leads)
        row["pdfs"] = sum(1 for lead in leads if lead.kind == "pdf")
        if not row["detail"] and events:
            row["detail"] = render(events[-1], "ru")
        if record_dir and rec.saved and row["status"] != "ok":      # нужны ответы там, где что-то не так
            for n, (url, status, body) in enumerate(rec.saved[:3]):
                fn = "%s_%d.bin" % (_safe(entry.id), n)
                with open(os.path.join(record_dir, fn), "wb") as f:
                    f.write(body)
                row["saved"].append({"file": fn, "url": url, "status": status, "bytes": len(body)})
    return rows


def summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def admin_domains(rows: List[Dict[str, Any]]) -> List[str]:
    """Домены источников, которые не отвечают (error, quota не считается: сайт доступен) — для запроса администраторам."""
    return sorted(set(d for r in rows if r["status"] == "error" for d in r["domains"]))
