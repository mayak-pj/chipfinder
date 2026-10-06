# -*- coding: utf-8 -*-
"""Статистика поиска (ARCHITECTURE §4.10; шаг 5.4): что и как быстро находит каждый источник.

* `StatsRecorder` — подписчик `EventBus`: `begin(партномер)` открывает поиск, дальше счётчики копятся по событиям
  (запросы, ссылки, скачано, капча, ошибки, вердикты), итог `result.*` (или `search.cancelled`) закрывает поиск
  и пишет его в SQLite. События вне открытого поиска игнорируются; незакрытый поиск при новом `begin`
  записывается как прерванный.
* Вердикт без источника (`result.confirmed` не знает, чей документ) относится к источнику последнего
  скачивания. Первым источником поиска считается тот, чей документ дал `confirmed`.
* `SearchStats` — таблицы `search_runs`, `source_runs` и показатели: доля успеха, среднее время до
  подтверждения, запросов на успех, доля капчи и ошибок — в целом и по семейству / языку / производителю.

Время — из `clock` (тесты не ждут). Записи — короткие транзакции под блокировкой `SQLiteLocalDB`.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .events import Event, EventBus
from .query import base_part, family as part_family

QUERY_KEYS = ("engine.query", "site.search", "market.search", "maker.search")
LINK_KEYS = ("engine.found", "site.found", "site.found_pdf", "market.found", "maker.found", "crawl.found")
ERROR_KEYS = ("engine.error", "engine.quota", "fetch.failed", "access.network_blocked", "access.transient",
              "access.unknown")
CAPTCHA_KEYS = ("engine.captcha", "access.site_protected")
RESULT_STATUS = {"result.confirmed": "confirmed", "result.probable": "probable", "result.needs_user": "needs_user",
                 "result.rejected": "rejected", "result.not_found": "not_found", "search.cancelled": "cancelled"}
GROUPS = {"family": "family", "lang": "lang", "maker": "maker"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS search_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL, part TEXT, family TEXT, maker TEXT, level TEXT,
  status TEXT, queries INTEGER, links INTEGER, downloaded INTEGER, confirmed INTEGER, probable INTEGER,
  rejected INTEGER, errors INTEGER, captcha INTEGER, t_first_link REAL, t_confirm REAL, first_source TEXT);
CREATE TABLE IF NOT EXISTS source_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, source TEXT, lang TEXT, family TEXT, maker TEXT,
  queries INTEGER, links INTEGER, downloaded INTEGER, confirmed INTEGER, probable INTEGER, rejected INTEGER,
  errors INTEGER, captcha INTEGER, t_first_link REAL, t_confirm REAL);
CREATE INDEX IF NOT EXISTS source_runs_source ON source_runs(source);
"""
RUN_COLUMNS = ("id", "started_at", "part", "family", "maker", "level", "status", "queries", "links", "downloaded",
               "confirmed", "probable", "rejected", "errors", "captcha", "t_first_link", "t_confirm", "first_source")
SOURCE_COLUMNS = ("id", "run_id", "source", "lang", "family", "maker", "queries", "links", "downloaded", "confirmed",
                  "probable", "rejected", "errors", "captcha", "t_first_link", "t_confirm")
COUNTERS = ("queries", "links", "downloaded", "confirmed", "probable", "rejected", "errors", "captcha")
METRIC_COLUMNS = ("source", "group", "attempts", "success_rate", "avg_confirm_time", "queries_per_success",
                  "captcha_rate", "error_rate")


class SearchStats:
    def __init__(self, db, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        with db._lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()

    def _all(self, sql: str, args: tuple = ()) -> List[tuple]:
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchall()

    # -------------------- запись --------------------
    def save_run(self, run: Dict[str, Any], sources: List[Dict[str, Any]]) -> int:
        cols = [c for c in RUN_COLUMNS if c != "id"]
        with self.db._lock:
            cur = self.db.conn.execute(
                "INSERT INTO search_runs(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))),
                tuple(run.get(c) for c in cols))
            run_id = cur.lastrowid
            scols = [c for c in SOURCE_COLUMNS if c != "id"]
            for s in sources:
                s = dict(s, run_id=run_id)
                self.db.conn.execute(
                    "INSERT INTO source_runs(%s) VALUES(%s)" % (",".join(scols), ",".join("?" * len(scols))),
                    tuple(s.get(c) for c in scols))
            self.db.conn.commit()
        return run_id

    # -------------------- чтение --------------------
    def runs(self, limit: int = 0) -> List[Dict[str, Any]]:
        sql = "SELECT %s FROM search_runs ORDER BY id" % ",".join(RUN_COLUMNS)
        if limit:
            sql += " DESC LIMIT %d" % int(limit)
        return [dict(zip(RUN_COLUMNS, r)) for r in self._all(sql)]

    def source_runs(self, source: str = "") -> List[Dict[str, Any]]:
        sql = "SELECT %s FROM source_runs" % ",".join(SOURCE_COLUMNS)
        args: tuple = ()
        if source:
            sql += " WHERE source=?"
            args = (source,)
        return [dict(zip(SOURCE_COLUMNS, r)) for r in self._all(sql + " ORDER BY id", args)]

    def search_count(self) -> int:
        """Сколько поисков записано (адаптивный порядок включается после 20)."""
        return self._all("SELECT COUNT(*) FROM search_runs")[0][0]

    def metrics(self, by: str = "", source: str = "") -> List[Dict[str, Any]]:
        """Показатели источников: в целом (`by=""`) или в разрезе `family` / `lang` / `maker`.

        success_rate — подтверждено / попыток; queries_per_success и avg_confirm_time — None, если успехов нет;
        captcha_rate и error_rate — доля попыток с капчей / ошибкой.
        """
        if by and by not in GROUPS:
            raise ValueError("metrics(by=): «%s», допустимо %s" % (by, "|".join(GROUPS)))
        group = GROUPS.get(by, "''")
        where, args = ("WHERE source=?", (source,)) if source else ("", ())
        rows = self._all(
            "SELECT source, %s AS grp, COUNT(*), SUM(confirmed>0), SUM(confirmed), SUM(queries), SUM(captcha>0), "
            "SUM(errors>0), AVG(CASE WHEN confirmed>0 THEN t_confirm END) FROM source_runs %s "
            "GROUP BY source, grp ORDER BY source, grp" % (group, where), args)
        out = []
        for src, grp, attempts, wins, confirmed, queries, captcha, errors, avg_t in rows:
            out.append({"source": src, "group": grp, "attempts": attempts,
                        "success_rate": wins / attempts,
                        "avg_confirm_time": avg_t if wins else None,
                        "queries_per_success": queries / confirmed if confirmed else None,
                        "captcha_rate": captcha / attempts, "error_rate": errors / attempts})
        return out

    def fastest(self, by: str = "") -> List[Dict[str, Any]]:
        """Источники, у которых были успехи, от самого быстрого по среднему времени до подтверждения."""
        rows = [m for m in self.metrics(by=by) if m["avg_confirm_time"] is not None]
        return sorted(rows, key=lambda m: (m["avg_confirm_time"], m["source"]))

    def export_csv(self, by: str = "") -> str:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(METRIC_COLUMNS)
        for m in self.metrics(by=by):
            w.writerow(["" if m[c] is None else (round(m[c], 3) if isinstance(m[c], float) else m[c])
                        for c in METRIC_COLUMNS])
        return buf.getvalue()


class StatsRecorder:
    """Подписчик шины: собирает счётчики открытого поиска и пишет их в `SearchStats` при закрытии."""

    def __init__(self, stats: SearchStats, bus: Optional[EventBus] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.stats = stats
        self.clock = clock or stats.clock
        self._lock = threading.RLock()
        self._run: Optional[Dict[str, Any]] = None
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._last_fetch = ""
        self._unsubscribe = bus.subscribe(self.on_event) if bus is not None else None

    def close(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None

    def begin(self, part: str, maker: str = "", level: str = "") -> None:
        with self._lock:
            if self._run is not None:
                self._finish("cancelled")
            self._run = {"started_at": self.clock(), "part": part, "maker": maker or "", "level": level,
                         "family": part_family(part) or base_part(part), "first_source": ""}
            self._sources = {}
            self._last_fetch = ""

    def _source(self, e: Event) -> Dict[str, Any]:
        s = self._sources.get(e.source)
        if s is None:
            s = self._sources[e.source] = dict(
                source=e.source, lang=e.lang, family=self._run["family"], maker=self._run["maker"],
                t_first_link=None, t_confirm=None, **{c: 0 for c in COUNTERS})
        return s

    def on_event(self, e: Event) -> None:
        with self._lock:
            if self._run is None:
                return
            key, now = e.key, self.clock()
            if key in RESULT_STATUS:
                self._verdict(e, RESULT_STATUS[key], now)
                return
            if not e.source:
                return
            if key in QUERY_KEYS:
                self._source(e)["queries"] += 1
            elif key in LINK_KEYS:
                s = self._source(e)
                n = int(e.params.get("n", 1) or 0)
                s["links"] += n
                if n and s["t_first_link"] is None:
                    s["t_first_link"] = now - self._run["started_at"]
            elif key == "fetch.done":
                self._source(e)["downloaded"] += 1
                self._last_fetch = e.source
            elif key == "verify.rejected":
                self._source(e)["rejected"] += 1
            elif key in CAPTCHA_KEYS:
                self._source(e)["captcha"] += 1
            elif key in ERROR_KEYS:
                self._source(e)["errors"] += 1

    def _verdict(self, e: Event, status: str, now: float) -> None:
        winner = e.source or self._last_fetch
        if status in ("confirmed", "probable") and winner:
            s = self._source(Event(e.key, source=winner, lang=e.lang))
            s[status] += 1
            s["t_confirm"] = now - self._run["started_at"]
            if status == "confirmed" and not self._run["first_source"]:
                self._run["first_source"] = winner
        self._finish(status)

    def _finish(self, status: str) -> None:
        run, sources = self._run, list(self._sources.values())
        self._run, self._sources = None, {}
        run["status"] = status
        for c in COUNTERS:
            run[c] = sum(s[c] for s in sources)
        firsts = [s["t_first_link"] for s in sources if s["t_first_link"] is not None]
        run["t_first_link"] = min(firsts) if firsts else None
        confirms = [s["t_confirm"] for s in sources if s["t_confirm"] is not None]
        run["t_confirm"] = min(confirms) if confirms and status in ("confirmed", "probable") else None
        self.stats.save_run(run, sources)
