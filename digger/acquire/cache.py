# -*- coding: utf-8 -*-
"""Кэши поиска (ARCHITECTURE §4.9; шаг 5.3): выдача, негативный кэш, здоровье доменов.

* `search_cache` — ссылки, найденные источником по запросу (7 дней). Пустая выдача не кэшируется.
* `negative_cache` — «по этому партномеру ничего не найдено» (30 дней), по уровню источников.
* `domain_health` — подряд идущие неудачи домена; после второй неудачи (капчи — сразу) домен «отдыхает»,
  срок растёт вдвое с каждой следующей неудачей, но не больше суток. Успех сбрасывает отдых.

Время берётся из `clock` — тесты сдвигают его без `sleep`. Записи — короткие транзакции под блокировкой
`SQLiteLocalDB`, как в `store.py`; таблицы создаёт `IF NOT EXISTS`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from ..core.utils import norm_part
from .models import Lead

DAY = 86400.0
SEARCH_TTL = 7 * DAY
NEGATIVE_TTL = 30 * DAY
REST_BASE = 60.0              # отдых после 2-й неудачи подряд, с
REST_CAPTCHA = 30 * 60.0      # отдых после капчи, с
REST_FAILS_FROM = 2
MAX_FAILS = 20
SCHEMA = """
CREATE TABLE IF NOT EXISTS search_cache(
  key TEXT PRIMARY KEY, source TEXT, query TEXT, leads TEXT, saved_at REAL);
CREATE TABLE IF NOT EXISTS negative_cache(
  part TEXT, level TEXT, reason TEXT, saved_at REAL, PRIMARY KEY(part, level));
CREATE TABLE IF NOT EXISTS domain_health(
  domain TEXT PRIMARY KEY, fails INTEGER DEFAULT 0, ok_total INTEGER DEFAULT 0, fail_total INTEGER DEFAULT 0,
  last_error TEXT, last_at REAL, rest_until REAL DEFAULT 0);
"""


def _norm_query(q: str) -> str:
    return " ".join(q.lower().split())


class AcquireCache:
    def __init__(self, db, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        with db._lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()

    def _run(self, sql: str, args: tuple = ()) -> Any:
        with self.db._lock:
            cur = self.db.conn.execute(sql, args)
            self.db.conn.commit()
            return cur

    def _one(self, sql: str, args: tuple = ()) -> Optional[tuple]:
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchone()

    # -------------------- кэш выдачи --------------------
    @staticmethod
    def _search_key(source: str, query: str) -> str:
        return "%s\n%s" % (source, _norm_query(query))

    def get_search(self, source: str, query: str, ttl: float = SEARCH_TTL) -> Optional[List[Lead]]:
        row = self._one("SELECT leads, saved_at FROM search_cache WHERE key=?", (self._search_key(source, query),))
        if not row or self.clock() - row[1] >= ttl:
            return None
        return [Lead.from_dict(d) for d in json.loads(row[0])]

    def put_search(self, source: str, query: str, leads: List[Lead]) -> None:
        if not leads:
            return
        self._run("INSERT OR REPLACE INTO search_cache(key, source, query, leads, saved_at) VALUES(?,?,?,?,?)",
                  (self._search_key(source, query), source, _norm_query(query),
                   json.dumps([l.to_dict() for l in leads], ensure_ascii=False), self.clock()))

    # -------------------- негативный кэш --------------------
    def is_not_found(self, part: str, level: str = "", ttl: float = NEGATIVE_TTL) -> bool:
        row = self._one("SELECT saved_at FROM negative_cache WHERE part=? AND level=?", (norm_part(part), level))
        return bool(row) and self.clock() - row[0] < ttl

    def mark_not_found(self, part: str, level: str = "", reason: str = "") -> None:
        self._run("INSERT OR REPLACE INTO negative_cache(part, level, reason, saved_at) VALUES(?,?,?,?)",
                  (norm_part(part), level, reason, self.clock()))

    def forget_not_found(self, part: str) -> None:
        self._run("DELETE FROM negative_cache WHERE part=?", (norm_part(part),))

    # -------------------- здоровье доменов --------------------
    @staticmethod
    def rest_seconds(fails: int, captcha: bool = False) -> float:
        if captcha:
            return REST_CAPTCHA
        if fails < REST_FAILS_FROM:
            return 0.0
        n = min(fails, MAX_FAILS) - REST_FAILS_FROM
        return min(REST_BASE * (2 ** n), DAY)

    def record_domain(self, domain: str, ok: bool, error: str = "") -> None:
        now = self.clock()
        if ok:
            self._run("INSERT INTO domain_health(domain, fails, ok_total, last_at, rest_until) VALUES(?,0,1,?,0) "
                      "ON CONFLICT(domain) DO UPDATE SET fails=0, ok_total=ok_total+1, last_at=?, rest_until=0",
                      (domain, now, now))
            return
        row = self._one("SELECT fails FROM domain_health WHERE domain=?", (domain,))
        fails = (row[0] if row else 0) + 1
        rest = self.rest_seconds(fails, captcha=(error == "captcha"))
        self._run("INSERT INTO domain_health(domain, fails, fail_total, last_error, last_at, rest_until) "
                  "VALUES(?,?,1,?,?,?) ON CONFLICT(domain) DO UPDATE SET fails=?, fail_total=fail_total+1, "
                  "last_error=?, last_at=?, rest_until=?",
                  (domain, fails, error, now, now + rest if rest else 0, fails, error, now, now + rest if rest else 0))

    def is_resting(self, domain: str) -> bool:
        row = self._one("SELECT rest_until FROM domain_health WHERE domain=?", (domain,))
        return bool(row) and row[0] > self.clock()

    def domain_health(self, domain: str) -> Dict[str, Any]:
        row = self._one("SELECT fails, ok_total, fail_total, last_error, last_at, rest_until "
                        "FROM domain_health WHERE domain=?", (domain,))
        keys = ("fails", "ok_total", "fail_total", "last_error", "last_at", "rest_until")
        return dict(zip(keys, row)) if row else dict(zip(keys, (0, 0, 0, "", 0.0, 0.0)))

    # -------------------- чистка --------------------
    def purge(self) -> int:
        """Удаляет устаревшие записи кэша выдачи и негативного кэша; возвращает их число."""
        now = self.clock()
        n = self._run("DELETE FROM search_cache WHERE saved_at<=?", (now - SEARCH_TTL,)).rowcount
        n += self._run("DELETE FROM negative_cache WHERE saved_at<=?", (now - NEGATIVE_TTL,)).rowcount
        return n
