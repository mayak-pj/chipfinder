# -*- coding: utf-8 -*-
"""Пробное получение документов для эталонных чипов (шаг 3.3a; проверка `downloads` набора Win7).

Короткий путь без оркестратора (он — фаза 6): источники уровней `LEVELS` по порядку → ранжирование → для лучших
ссылок: PDF скачивается в карантин и проверяется, страница обходится (`crawl`), и PDF берётся с неё. Если вместо
PDF пришла страница — она тоже обходится. Цель — узнать из сети работы, что скачивается и откуда, а не найти
лучший документ: с одного домена — одна попытка, чип закрыт после `GOOD` разных годных PDF.

Источник, дважды подряд ответивший капчей/ошибкой, для следующих чипов пропускается. Скачанные файлы после
проверки удаляются (в отчёт PDF не попадают); страницы, на которых PDF не нашёлся, сохраняются в `record_dir`.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from ..core.netsafe import host_of
from .crawl import crawl
from .diagnose import SAVE_BYTES, _safe, _status_of
from .events import EventBus, render
from .fetch import fetch_to_quarantine
from .models import Lead
from .query import Query, base_part
from .rank import normalize_url, score_lead
from .registry import Registry
from .validate import validate_pdf

log = logging.getLogger("chipfinder.acquire.trial")
# 12 эталонных чипов (ARCHITECTURE §9)
CHIPS = ("NE555", "LM358", "AT24C02", "W25Q64JV", "STM32F103C8T6", "GD32F103C8T6", "CH340G", "AMS1117", "ESP8266EX",
         "PMS150C", "STC89C52RC", "74HC595")
LEVELS = ("catalog", "maker", "search", "china", "russian")
ENOUGH_LEADS = 8          # ссылок хватает — остальные источники для чипа не опрашиваются
ENOUGH_PDFS = 3
GOOD = 2                  # годных PDF на чип
MAX_FETCH = 4             # скачиваний на чип
MAX_CRAWL = 3             # обходов страниц на чип
PER_PAGE = 2              # PDF с одной страницы
STRIKES = 2               # неудач источника подряд, после которых он пропускается
CHIP_SEC = 150.0
LOG_LINES = 80
_BAD = ("captcha", "offtopic", "quota", "error", "no_key", "parse_error")


class _Pages:
    """Обёртка над SafeHttp: запоминает страницы, открытые обходом."""

    def __init__(self, http: Any):
        self._http = http
        self.saved: List[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._http, name)

    def get_html(self, url: str, referer: str = "") -> Any:
        final, text = self._http.get_html(url, referer=referer)
        self.saved.append((final, text.encode("utf-8")[:SAVE_BYTES]))
        return final, text


def _is_pdf(lead: Lead) -> bool:
    return lead.kind == "pdf" or urlsplit(lead.url).path.lower().endswith(".pdf")


def _collect(adapters: List[Any], part: str, http: Any, events: List[Any], strikes: Dict[str, int],
             over: Callable[[], bool]) -> Any:
    """Опрос источников по порядку уровней: ([Lead], [строка по источнику])."""
    leads: List[Lead] = []
    rows: List[Dict[str, Any]] = []
    for ad in adapters:
        if over() or (len(leads) >= ENOUGH_LEADS and sum(1 for x in leads if _is_pdf(x)) >= ENOUGH_PDFS):
            break
        if hasattr(ad, "applies") and not ad.applies(part):       # сайт другого производителя
            continue
        if strikes.get(ad.id, 0) >= STRIKES:
            rows.append({"id": ad.id, "status": "skipped", "leads": 0, "pdfs": 0})
            continue
        text = part + " datasheet pdf" if ad.family == "engine" else part
        start = len(events)
        try:
            got = ad.search(Query("en", text, "part"), http)
            status = _status_of(events[start:])
        except Exception as e:       # noqa: BLE001 — адаптер упал на ответе сайта; остальные источники работают
            log.warning("проба: %s упал: %s", ad.id, e)
            got, status = [], "parse_error"
        strikes[ad.id] = strikes.get(ad.id, 0) + 1 if status in _BAD else 0
        leads += got
        rows.append({"id": ad.id, "status": status, "leads": len(got), "pdfs": sum(1 for x in got if _is_pdf(x))})
    return leads, rows


def trial_downloads(sources_path: Optional[str], http: Any, keys: Optional[Dict[str, Any]] = None,
                    parts: Sequence[str] = CHIPS, progress: Optional[Callable[[str], None]] = None,
                    cancel: Any = None, record_dir: Optional[str] = None, data: Optional[Dict[str, Any]] = None,
                    adapters: Optional[Dict[str, Any]] = None, clock: Callable[[], float] = time.time,
                    sleep: Callable[[float], None] = time.sleep, chip_sec: float = CHIP_SEC) -> List[Dict[str, Any]]:
    """Строки по чипам: part, status (ok | invalid | no_pdf | no_leads), valid, sources, leads, attempts,
    not_whitelisted, budget (не хватило времени), seconds, log (ход поиска по-русски)."""
    say = progress or (lambda m: None)
    bus = EventBus()
    events: List[Any] = []
    bus.subscribe(events.append)
    kw = dict(keys=keys, bus=bus, adapters=adapters)
    reg = Registry(data, **kw) if data is not None else Registry.load(sources_path, **kw)
    built = [ad for ad in reg.build() if ad.level in LEVELS]
    levels = [lv["id"] for lv in reg.levels()]
    makers = list(reg.data.get("maker_sites", {}).values())
    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
    strikes: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []

    for n, part in enumerate(parts):
        if cancel is not None and getattr(cancel, "cancelled", False):
            break
        say("Проба %d/%d: %s" % (n + 1, len(parts), part))
        del events[:]
        t0 = clock()
        attempts: List[Dict[str, Any]] = []
        foreign: List[str] = []
        refused: List[Dict[str, str]] = []
        row = {"part": part, "status": "", "valid": 0, "sources": [], "leads": 0, "attempts": attempts,
               "not_whitelisted": foreign, "not_whitelisted_urls": refused, "budget": False, "seconds": 0.0, "log": []}
        rows.append(row)

        def over(share: float = 1.0) -> bool:
            if clock() - t0 > chip_sec * share:
                row["budget"] = True
                return True
            return bool(cancel is not None and getattr(cancel, "cancelled", False))

        found, row["sources"] = _collect(built, part, http, events, strikes, lambda: over(0.6))
        ranked: List[Lead] = []       # не `rank()`: он заменяет адрес нормализованным (без `www.`), а качать надо исходный
        seen = set()
        for lead in found:
            key = normalize_url(lead.url)
            if key not in seen:
                seen.add(key)
                ranked.append(replace(lead, rank_score=score_lead(lead, part, levels, makers, base_part(part))))
        ranked.sort(key=lambda x: -x.rank_score)
        row["leads"] = len(ranked)

        def allowed(lead: Lead) -> bool:
            if http.is_allowed(lead.url):
                return True
            if host_of(lead.url) not in foreign:
                foreign.append(host_of(lead.url))
            if lead.url not in [r["url"] for r in refused]:
                refused.append({"url": lead.url, "host": host_of(lead.url), "source": lead.source_id,
                                "reason": "http" if lead.url.startswith("http://") else "domain"})
            return False

        def download(lead: Lead, from_page: str = "") -> Dict[str, Any]:
            start = clock()
            referer = from_page or (lead.snippet if lead.snippet.startswith("http") else "")
            res = fetch_to_quarantine(http, lead, bus=bus, referer=referer, attempts=2, pause=3.0, sleep=sleep)
            a = {"step": "fetch", "url": lead.url, "host": host_of(lead.url), "source": lead.source_id,
                 "from_page": from_page, "ok": res.ok, "failure_class": res.failure_class, "error": res.error,
                 "final_url": res.final_url, "size": res.size, "sha256": res.sha256, "valid": False, "reason": "",
                 "pages": 0, "has_text": False, "active": [], "detail": ""}
            if res.ok:
                v = validate_pdf(res.path_in_quarantine, bus=bus, lead=lead)
                a.update(valid=v.ok, reason=v.reason, pages=v.pages, has_text=v.has_text, active=v.active,
                         detail=v.detail)
                try:
                    os.remove(res.path_in_quarantine)
                except OSError:
                    pass
            a["seconds"] = round(clock() - start, 2)
            attempts.append(a)
            tried.add(normalize_url(lead.url))
            if a["valid"] and a["sha256"] not in good:      # тот же файл с другого адреса вторым не считается
                good.add(a["sha256"])
                good_hosts.add(a["host"])
                row["valid"] += 1
            return a

        fetches = crawls = 0
        pdf_hosts, page_hosts, good_hosts, tried, good = set(), set(), set(), set(), set()
        for lead in ranked:
            if row["valid"] >= GOOD or over():
                break
            if lead.url.startswith("http://") and http.is_allowed("https://" + lead.url[7:]):
                lead = replace(lead, url="https://" + lead.url[7:])      # ссылка по http на разрешённый сайт — по https
            if not allowed(lead):
                continue
            host = host_of(lead.url)
            page = lead
            force = False
            if _is_pdf(lead):
                if host in pdf_hosts or fetches >= MAX_FETCH:
                    continue
                pdf_hosts.add(host)
                fetches += 1
                a = download(lead)
                if a["ok"] or "не PDF" not in a["error"]:
                    continue
                page, force = replace(lead, kind="page"), True       # вместо файла пришла страница: ищем PDF на ней
            if host in page_hosts or host in good_hosts or crawls >= MAX_CRAWL:
                continue
            page_hosts.add(host)
            crawls += 1
            start, mark = clock(), len(events)
            rec = _Pages(http)
            pdfs = crawl(rec, page, bus=bus, force=force)
            a = {"step": "crawl", "url": page.url, "host": host, "source": lead.source_id, "ok": bool(pdfs),
                 "found": len(pdfs), "detail": render(events[-1], "ru") if len(events) > mark else "", "saved": [],
                 "seconds": round(clock() - start, 2)}
            attempts.append(a)
            if not pdfs and record_dir:
                for i, (url, body) in enumerate(rec.saved[:2]):
                    name = "%s_%s_%d.html" % (_safe(part), _safe(host), i)
                    with open(os.path.join(record_dir, name), "wb") as f:
                        f.write(body)
                    a["saved"].append(name)
            for pdf in [p for p in pdfs if normalize_url(p.url) not in tried][:PER_PAGE]:
                if row["valid"] >= GOOD or fetches >= MAX_FETCH or over():
                    break
                if allowed(pdf):
                    pdf_hosts.add(host_of(pdf.url))
                    fetches += 1
                    download(pdf, from_page=pdf.snippet)

        downloaded = [a for a in attempts if a["step"] == "fetch" and a["ok"]]
        row["status"] = ("ok" if row["valid"] else "invalid" if downloaded else "no_pdf" if ranked else "no_leads")
        row["seconds"] = round(clock() - t0, 2)
        row["log"] = [render(e, "ru") for e in events[-LOG_LINES:]]
    return rows


def summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def host_table(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Скачивания по доменам: сколько попыток, скачано, годных и классы неудач (§4.11)."""
    by: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        for a in r["attempts"]:
            if a["step"] != "fetch":
                continue
            h = by.setdefault(a["host"], {"host": a["host"], "tried": 0, "downloaded": 0, "valid": 0, "failures": {}})
            h["tried"] += 1
            h["downloaded"] += int(a["ok"])
            h["valid"] += int(a["valid"])
            if not a["ok"]:
                cls = a["failure_class"] or "other"
                h["failures"][cls] = h["failures"].get(cls, 0) + 1
    return sorted(by.values(), key=lambda h: h["host"])
