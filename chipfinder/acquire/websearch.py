# -*- coding: utf-8 -*-
"""Интерфейс `WebSearch` поверх оркестратора (шаг 6.5): GUI и конвейер те же, поиск — `acquire/`."""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from ..core.config import resolve_path
from ..core.interfaces import CancelToken, ProgressFn, WebSearch
from ..core.models import Candidate, DatasheetHit, DownloadResult
from ..core.netsafe import NetBlocked, SafeHttp, host_of
from ..core.utils import norm_part
from .events import render
from .models import AcquisitionRecord, PhotoContext
from .registry import Registry

PDF_LINK = re.compile(r'(?:href|src|data)=["\']([^"\']+\.pdf[^"\']*)', re.I)


def looks_pdf(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(".pdf") or "/pdf/" in path or path.endswith("/pdf") or ".pdf?" in url.lower()


class AcquireWebSearch(WebSearch):
    name = "acquire"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        quarantine = resolve_path(ctx.app_dir, ctx.config["paths"]["quarantine_dir"])
        self.http = SafeHttp(dict(ctx.config.get("network", {})), quarantine, ctx.log)
        self.registry = Registry.load(os.path.join(ctx.app_dir, "data", "sources.json"))
        self.http.add_allowed(self.registry.allowed_domains())
        self._orch: Any = None
        self.last_result: Any = None
        self._cleanup_quarantine(quarantine, days=int(self.settings.get("quarantine_keep_days", 7)))

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

    @property
    def orchestrator(self) -> Any:
        if self._orch is None:
            from .orchestrator import from_context
            self._orch = from_context(self.ctx, bus=getattr(self.ctx, "bus", None), http=self.http)
        return self._orch

    # -------------------- поиск --------------------
    def _contexts(self, candidates: List[Candidate]) -> List[PhotoContext]:
        parts = [c for c in candidates if not c.is_marking_code][:int(self.settings.get("max_parts", 2))]
        codes = [c for c in candidates if c.is_marking_code][:1]
        marking = codes[0].part if codes else ""
        if not parts:
            return [PhotoContext(marking=marking)] if marking else []
        return [PhotoContext(part=c.part, manufacturer=c.manufacturer, marking=marking) for c in parts]

    def _hits(self, res: Any) -> List[DatasheetHit]:
        out: Dict[str, DatasheetHit] = {}

        def add(h: DatasheetHit) -> None:
            old = out.get(h.location)
            if old is None or old.score < h.score:
                out[h.location] = h

        if res.path:
            add(DatasheetHit(part=res.part, title=os.path.basename(res.path), location=res.path, source="library",
                             level="local", score=1.0, is_local=True, is_pdf=True, note="из своей библиотеки"))
        for rec in res.records:
            h = self._hit(rec)
            if h is not None:
                add(h)
        return sorted(out.values(), key=lambda h: (-h.score, not h.is_pdf))[:int(self.settings.get("max_hits", 60))]

    def _hit(self, rec: AcquisitionRecord) -> Optional[DatasheetHit]:
        v, lead = rec.verdict, rec.lead
        if lead is None or (v is not None and v.status == "rejected"):
            return None
        status = v.status if v is not None else "probable"
        stored = rec.stored_path if status == "confirmed" else ""
        url = stored or lead.url
        title = (lead.title or "").strip()[:160] or urlsplit(lead.url).path.rsplit("/", 1)[-1] or lead.url
        score = (v.score / 100.0) if v is not None else 0.3
        return DatasheetHit(part=rec.part, title=title, location=url, source=lead.source_id, level=lead.level,
                            score=round(score, 3), is_local=bool(stored), is_pdf=bool(stored) or lead.kind == "pdf"
                            or looks_pdf(lead.url), allowed=bool(stored) or self.http.is_allowed(lead.url),
                            note="подтверждён" if stored else status)

    def search(self, candidates: List[Candidate], progress: Optional[ProgressFn] = None,
               cancel: Optional[CancelToken] = None, levels: Optional[List[str]] = None) -> List[DatasheetHit]:
        say = progress or (lambda m: None)
        if self.http.offline:
            say("Интернет-поиск выключен (автономный режим)")
            return []
        orch = self.orchestrator
        everywhere = bool(levels and "all" in levels)
        unsubscribe = orch.bus.subscribe(lambda e: say(render(e)))
        done = threading.Event()
        if cancel is not None:
            def watch() -> None:
                while not done.wait(0.1):
                    if cancel.cancelled:
                        orch.cancel()
                        return
            threading.Thread(target=watch, daemon=True).start()
        hits: List[DatasheetHit] = []
        try:
            for pc in self._contexts(candidates):
                if cancel is not None and cancel.cancelled:
                    break
                res = orch.search(pc, everywhere=everywhere)
                self.last_result = res
                hits += self._hits(res)
                if res.conclusion is not None and res.status in ("not_found", "rejected"):
                    say(res.conclusion.text())
                if res.status == "confirmed" and not everywhere:
                    break
        finally:
            done.set()
            unsubscribe()
        return sorted(hits, key=lambda h: (-h.score, not h.is_pdf))

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
                    urls += self._inner_pdfs(url)
                continue
            except Exception as ex:  # noqa: BLE001
                last_err = "%s: %s" % (type(ex).__name__, ex)
                continue
            res = DownloadResult(True, path=path, sha256=sha, size=size, suspicious=danger)
            pdf = self.ctx.module("pdf_text")
            if pdf is not None:
                try:
                    p = norm_part(hit.part)
                    nt = norm_part(pdf.extract(path, max_pages=6))
                    res.verified_part = bool(p) and (p in nt or (len(p) >= 6 and p[:max(5, int(len(p) * 0.7))] in nt))
                except Exception as ex:  # noqa: BLE001
                    res.message = "Текст PDF не читается: %s" % ex
            if danger:
                res.message = ("ВНИМАНИЕ: в PDF есть активное содержимое (%s). Файл оставлен в карантине."
                               % ", ".join(danger))
            return res
        return DownloadResult(False, message=last_err or "не удалось скачать")

    def _inner_pdfs(self, url: str) -> List[str]:
        """Страница-обёртка: ссылка или iframe на PDF внутри неё."""
        try:
            final, html = self.http.get_html(url)
        except Exception:  # noqa: BLE001
            return []
        found = [urljoin(final, re.sub(r"#.*$", "", m)) for m in PDF_LINK.findall(html)]
        return [u for u in found if self.http.is_allowed(u)][:3]

    # -------------------- диагностика --------------------
    def diagnose(self, progress: Optional[ProgressFn] = None,
                 cancel: Optional[CancelToken] = None) -> List[Dict[str, Any]]:
        say = progress or (lambda m: None)
        checks = []
        seen = set()
        for en in self.registry.entries():
            if en.domains:
                checks.append((en.adapter, en.name or en.id, en.domains[0]))
                seen.add(en.domains[0])
        for d in self.registry.data.get("pdf_hosts", []):
            if d not in seen:
                checks.append(("Сайт", d, d))
        rows = []
        old_interval = self.http.min_interval
        self.http.min_interval = 0.2
        try:
            for i, (cat, name, domain) in enumerate(checks):
                if cancel and cancel.cancelled:
                    break
                url = "https://%s/" % (domain if domain.count(".") > 1 else "www." + domain)
                say("Проверка %d/%d: %s" % (i + 1, len(checks), name))
                ok, detail = self.http.probe(url)
                rows.append({"category": cat, "name": name, "url": url, "domain": host_of(url),
                             "ok": ok, "detail": detail})
        finally:
            self.http.min_interval = old_interval
        return rows
