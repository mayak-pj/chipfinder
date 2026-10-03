# -*- coding: utf-8 -*-
"""Многоуровневый интернет-поиск datasheet.

Уровни и источники описаны в data/sources.json (их можно менять без
программирования). Порядок по умолчанию:
  1. сайты-каталоги datasheet  2. сайт производителя  3. китайский интернет
  4. форумы инженеров  5. базы SMD-кодов маркировки  6. GitHub
Поиск останавливается, когда найдено достаточно хороших PDF (настройка
stop_after_good_hits), если не выбран режим «искать на всех уровнях».
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit

from ...acquire.registry import legacy_sources
from ...core.config import read_json, resolve_path
from ...core.interfaces import CancelToken, ProgressFn, WebSearch
from ...core.models import Candidate, DatasheetHit, DownloadResult
from ...core.netsafe import NetBlocked, SafeHttp, domain_match, host_of
from ...core.utils import norm_part
from .linkparse import decode_engine_link, extract_links, is_engine_internal

DS_WORDS = ("datasheet", "data sheet", "pdf", "数据手册", "规格书", "手册", "даташит", "документац", "спецификац")
LEVEL_WEIGHT = {"catalog": 0.1, "maker": 0.15, "china": 0.05, "forum": -0.05, "marking": 0.0, "github": -0.1}


def looks_pdf(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(".pdf") or "/pdf/" in path or path.endswith("/pdf") or ".pdf?" in url.lower()


class MultiLevelWebSearch(WebSearch):
    name = "multilevel"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        self.sources = legacy_sources(read_json(os.path.join(ctx.app_dir, "data", "sources.json")))
        net_cfg = dict(ctx.config.get("network", {}))
        quarantine = resolve_path(ctx.app_dir, ctx.config["paths"]["quarantine_dir"])
        self.http = SafeHttp(net_cfg, quarantine, ctx.log)
        allowed = []
        for e in self.sources.get("engines", {}).values():
            if e.get("enabled"):
                allowed += e.get("domains", [])
        for lvl in self.sources.get("levels", []):
            for d in lvl.get("direct", []):
                allowed += d.get("domains", [])
        allowed += self.sources.get("pdf_hosts", [])
        allowed += list(self.sources.get("maker_sites", {}).values())
        self.http.add_allowed(allowed)
        self.pdf_hosts = self.sources.get("pdf_hosts", [])
        self.dead_engines: Dict[str, str] = {}
        self._cleanup_quarantine(quarantine, days=int(settings.get("quarantine_keep_days", 7)))

    @staticmethod
    def _cleanup_quarantine(folder: str, days: int) -> None:
        limit = time.time() - days * 86400
        try:
            for fn in os.listdir(folder):
                p = os.path.join(folder, fn)
                if os.path.isfile(p) and os.path.getmtime(p) < limit:
                    os.remove(p)
        except OSError:
            pass

    # -------------------- оценка ссылок --------------------
    def _score(self, url: str, text: str, part: str, level: str, is_code: bool = False) -> float:
        p = norm_part(part)
        u = norm_part(url)
        t = norm_part(text)
        low = (text or "").lower()
        s = 0.0
        if is_code:
            if p and (p in t or p in u):
                s += 0.35
            if any(w in low for w in ("marking", "smd", "丝印", "маркиров", "code")):
                s += 0.25
            if s == 0:
                return 0.0
        else:
            if p in u:
                s += 0.45
            elif len(p) >= 6 and p[:len(p) - 2] in u:
                s += 0.3
            elif p in t:
                s += 0.3
            elif len(p) >= 6 and p[:6] in (u + t):
                s += 0.1
            else:
                return 0.0
        if looks_pdf(url):
            s += 0.3
        if any(w in low for w in DS_WORDS):
            s += 0.1
        if domain_match(host_of(url), self.pdf_hosts):
            s += 0.1
        s += LEVEL_WEIGHT.get(level, 0.0)
        return max(0.01, min(1.0, s))

    def _make_hit(self, url, text, part, source, level, is_code=False, note="") -> Optional[DatasheetHit]:
        sc = self._score(url, text, part, level, is_code)
        if sc <= 0:
            return None
        title = (text or "").strip()[:160] or urlsplit(url).path.rsplit("/", 1)[-1] or url
        return DatasheetHit(part=part, title=title, location=url, source=source, level=level,
                            score=round(sc, 3), is_pdf=looks_pdf(url), allowed=self.http.is_allowed(url),
                            note=note)

    # -------------------- поисковики --------------------
    def _engine_query(self, query: str, engine_ids: List[str], say: ProgressFn) -> Tuple[str, List[Tuple[str, str]]]:
        engines = self.sources.get("engines", {})
        per_query = int(self.settings.get("engines_per_query", 1))
        collected: List[Tuple[str, str]] = []
        used = []
        for eid in engine_ids:
            e = engines.get(eid)
            if not e or not e.get("enabled") or eid in self.dead_engines:
                continue
            url = e["url"].replace("{q}", quote_plus(query))
            say("%s: %s" % (e["name"], query))
            try:
                final, html = self.http.get_html(url)
            except NetBlocked as ex:
                self.dead_engines[eid] = str(ex)
                say("  %s недоступен: %s" % (e["name"], ex))
                continue
            except Exception as ex:  # noqa
                self.dead_engines[eid] = type(ex).__name__
                say("  %s недоступен: %s" % (e["name"], type(ex).__name__))
                continue
            links = []
            for href, text in extract_links(html, final):
                real = decode_engine_link(href, e.get("decoder", "plain"))
                if is_engine_internal(real):
                    continue
                links.append((real, text))
            if not links:
                # пустая выдача часто означает капчу/блокировку
                if re.search(r"captcha|robot|unusual traffic|验证|вы не робот", html, re.I):
                    self.dead_engines[eid] = "просит капчу"
                    say("  %s просит капчу — пропускаю" % e["name"])
                continue
            collected += links
            used.append(eid)
            if len(used) >= per_query:
                break
        return ",".join(used), collected

    # -------------------- прямые сайты --------------------
    def _direct(self, src: Dict[str, Any], part: str, level: str, say: ProgressFn,
                cancel: Optional[CancelToken]) -> List[DatasheetHit]:
        url = src["url"].replace("{part}", quote_plus(part))
        say("%s: %s" % (src["name"], part))
        try:
            final, html = self.http.get_html(url)
        except Exception as ex:  # noqa
            say("  %s: %s" % (src["name"], ex))
            return []
        hits = []
        pages = []
        for href, text in extract_links(html, final):
            if not domain_match(host_of(href), src.get("domains", [])) and not looks_pdf(href):
                continue
            h = self._make_hit(href, text, part, src["name"], level)
            if not h:
                continue
            hits.append(h)
            if not h.is_pdf and h.allowed:
                pages.append(h)
        # шаг вглубь: на странице детали ищем ссылку на PDF
        follow = int(src.get("follow", 0))
        if follow:
            pages.sort(key=lambda h: -h.score)
            for pg in pages[: int(self.settings.get("follow_pages", 3))]:
                if cancel and cancel.cancelled:
                    break
                try:
                    f2, html2 = self.http.get_html(pg.location, referer=final)
                except Exception:  # noqa
                    continue
                for href, text in extract_links(html2, f2):
                    if looks_pdf(href):
                        h = self._make_hit(href, text or pg.title, part, src["name"], level,
                                           note="со страницы " + pg.location)
                        if h:
                            h.score = min(1.0, h.score + 0.05)
                            hits.append(h)
        return hits

    def _github(self, api_url: str, part: str, say: ProgressFn) -> List[DatasheetHit]:
        url = api_url.replace("{part}", quote_plus(part))
        say("GitHub: %s" % part)
        try:
            data = self.http.get_json(url)
        except Exception as ex:  # noqa
            say("  GitHub: %s" % ex)
            return []
        out = []
        for it in (data.get("items") or [])[:10]:
            text = "%s %s" % (it.get("full_name", ""), it.get("description") or "")
            h = self._make_hit(it.get("html_url", ""), text, part, "GitHub", "github")
            if h:
                h.note = "репозиторий — смотрите вручную"
                out.append(h)
        return out

    # -------------------- главный метод --------------------
    def search(self, candidates: List[Candidate], progress: Optional[ProgressFn] = None,
               cancel: Optional[CancelToken] = None, levels: Optional[List[str]] = None) -> List[DatasheetHit]:
        say = progress or (lambda m: None)
        if self.http.offline:
            say("Интернет-поиск выключен (автономный режим)")
            return []
        max_parts = int(self.settings.get("max_parts", 2))
        parts = [c for c in candidates if not c.is_marking_code][:max_parts]
        codes = [c for c in candidates if c.is_marking_code][:1]
        stop_after = int(self.settings.get("stop_after_good_hits", 3))
        all_levels = levels is not None and "all" in levels
        maker_sites = self.sources.get("maker_sites", {})

        hits: Dict[str, DatasheetHit] = {}

        def add(h: Optional[DatasheetHit]):
            if h is None:
                return
            old = hits.get(h.location)
            if old is None or old.score < h.score:
                hits[h.location] = h

        def good_count() -> int:
            return sum(1 for h in hits.values() if h.is_pdf and h.allowed and h.score >= 0.75)

        for lvl in self.sources.get("levels", []):
            lid = lvl.get("id")
            if not lvl.get("enabled", True):
                continue
            if levels and not all_levels and lid not in levels:
                continue
            if cancel and cancel.cancelled:
                break
            say("— %s" % lvl.get("name", lid))
            targets = codes if lvl.get("for_codes") else parts
            for cand in targets:
                subst = {"{part}": cand.part, "{code}": cand.part,
                         "{maker_site}": maker_sites.get(cand.manufacturer, "")}
                is_code = bool(lvl.get("for_codes"))
                for src in lvl.get("direct", []):
                    if cancel and cancel.cancelled:
                        break
                    for h in self._direct(src, cand.part, lid, say, cancel):
                        add(h)
                if lvl.get("github_api"):
                    for h in self._github(lvl["github_api"], cand.part, say):
                        add(h)
                for qt in lvl.get("queries", []):
                    if cancel and cancel.cancelled:
                        break
                    if "{maker_site}" in qt and not subst["{maker_site}"]:
                        continue
                    q = qt
                    for k, v in subst.items():
                        q = q.replace(k, v)
                    used, links = self._engine_query(q, lvl.get("engines", []), say)
                    for href, text in links:
                        add(self._make_hit(href, text, cand.part, used or "поиск", lid, is_code))
            if not all_levels and not levels and good_count() >= stop_after:
                say("Найдено достаточно подходящих PDF — дальше не ищу (можно «искать везде»)")
                break

        if self.dead_engines:
            say("Недоступные поисковики: " + ", ".join("%s (%s)" % kv for kv in self.dead_engines.items()))
        return sorted(hits.values(), key=lambda h: (-h.score, not h.is_pdf))[: int(self.settings.get("max_hits", 60))]

    # -------------------- скачивание --------------------
    def download(self, hit: DatasheetHit, progress: Optional[ProgressFn] = None) -> DownloadResult:
        say = progress or (lambda m: None)
        if not hit.allowed:
            return DownloadResult(False, message="Сайт %s не в белом списке. Программа его не открывает; "
                                                 "откройте ссылку вручную или добавьте домен в настройки."
                                                 % host_of(hit.location))
        urls = [hit.location]
        tried = set()
        last_err = ""
        while urls and len(tried) < 4:
            url = urls.pop(0)
            if url in tried:
                continue
            tried.add(url)
            say("Скачиваю %s" % url)
            try:
                path, sha, size, danger = self.http.download_pdf(url)
            except NetBlocked as ex:
                last_err = str(ex)
                if "не PDF" in last_err:
                    # страница-обёртка: ищем внутри ссылку/iframe на PDF
                    try:
                        final, html = self.http.get_html(url)
                        inner = [u for u, _t in extract_links(html, final) if looks_pdf(u)]
                        inner += [re.sub(r"#.*$", "", m) for m in
                                  re.findall(r'(?:src|data)=["\']([^"\']+\.pdf[^"\']*)', html, re.I)]
                        from urllib.parse import urljoin
                        urls += [urljoin(final, u) for u in inner if self.http.is_allowed(urljoin(final, u))][:3]
                    except Exception:  # noqa
                        pass
                continue
            except Exception as ex:  # noqa
                last_err = "%s: %s" % (type(ex).__name__, ex)
                continue
            res = DownloadResult(True, path=path, sha256=sha, size=size, suspicious=danger)
            pdf = self.ctx.module("pdf_text")
            if pdf is not None:
                try:
                    text = pdf.extract(path, max_pages=6)
                    p = norm_part(hit.part)
                    nt = norm_part(text)
                    res.verified_part = bool(p) and (p in nt or (len(p) >= 6 and p[:max(5, int(len(p) * 0.7))] in nt))
                except Exception as ex:  # noqa
                    res.message = "Текст PDF не читается: %s" % ex
            if danger:
                res.message = ("ВНИМАНИЕ: в PDF есть активное содержимое (%s). Файл оставлен в карантине."
                               % ", ".join(danger))
            return res
        return DownloadResult(False, message=last_err or "не удалось скачать")

    # -------------------- диагностика --------------------
    def diagnose(self, progress: Optional[ProgressFn] = None,
                 cancel: Optional[CancelToken] = None) -> List[Dict[str, Any]]:
        say = progress or (lambda m: None)
        rows = []
        checks = []
        for eid, e in self.sources.get("engines", {}).items():
            checks.append(("Поисковик", e["name"], e["url"].replace("{q}", "NE555+datasheet")))
        for lvl in self.sources.get("levels", []):
            for d in lvl.get("direct", []):
                checks.append(("Каталог", d["name"], d["url"].replace("{part}", "NE555")))
            if lvl.get("github_api"):
                checks.append(("GitHub", "GitHub API", lvl["github_api"].replace("{part}", "NE555")))
        seen = set(host_of(c[2]) for c in checks)
        for d in self.pdf_hosts:
            if d not in seen:
                checks.append(("Сайт", d, "https://%s/" % (d if d.count(".") > 1 else "www." + d)))
        old_interval = self.http.min_interval
        self.http.min_interval = 0.2
        try:
            for i, (cat, name, url) in enumerate(checks):
                if cancel and cancel.cancelled:
                    break
                say("Проверка %d/%d: %s" % (i + 1, len(checks), name))
                ok, detail = self.http.probe(url)
                rows.append({"category": cat, "name": name, "url": url, "domain": host_of(url),
                             "ok": ok, "detail": detail})
        finally:
            self.http.min_interval = old_interval
        return rows
