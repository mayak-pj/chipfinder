# -*- coding: utf-8 -*-
"""Решения пользователя и доверие к доменам (ARCHITECTURE §4.5, §4.4 E9; шаг 5.8).

«Подтвердить» поднимает файл до `confirmed` (из `probable/` в `confirmed/`), «Отклонить» убирает его из
библиотеки и из индекса (файл уходит в `rejected/`, повторно в библиотеку он не попадёт: `is_rejected`).
Решение пишется в паспорт и в таблицу `user_decisions` (одно на sha256, последнее решение заменяет прежнее).
Доверие к домену считается по этой таблице: ≥ 2 отказов и не меньше вдвое больше, чем подтверждений → «плохая
репутация» (E9 −10); ≥ 3 подтверждений без отказов → как известный каталог (E9 +5). Домены производителей
плохими не становятся, сами не становятся «производителем» (правило подтверждения (а) не расширяется).
"""
from __future__ import annotations

import io
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .store import AcquireStore
from .verify import SourceTrust

CONFIRMED = "confirmed"
REJECTED = "rejected"
BAD_MIN_REJECTS = 2
GOOD_MIN_CONFIRMS = 3
SCHEMA = """
CREATE TABLE IF NOT EXISTS user_decisions(
  sha256 TEXT PRIMARY KEY, part TEXT, domain TEXT, decision TEXT, at REAL);
CREATE INDEX IF NOT EXISTS user_decisions_domain ON user_decisions(domain);
"""


def domain_of(url: str) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class Learner:
    def __init__(self, store: AcquireStore, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.db = store.db
        self.clock = clock
        with self.db._lock:
            self.db.conn.executescript(SCHEMA)
            self.db.conn.commit()

    # -------------------- решения --------------------
    def confirm(self, sha256: str) -> Optional[Dict[str, Any]]:
        """Пользователь подтвердил файл. Возвращает паспорт или None, если файла нет в библиотеке."""
        return self._decide(sha256, CONFIRMED)

    def reject(self, sha256: str) -> Optional[Dict[str, Any]]:
        return self._decide(sha256, REJECTED)

    def is_rejected(self, sha256: str) -> bool:
        return self._one("SELECT 1 FROM user_decisions WHERE sha256=? AND decision=?", (sha256, REJECTED)) is not None

    def _decide(self, sha: str, decision: str) -> Optional[Dict[str, Any]]:
        row = self._one("SELECT path FROM files WHERE sha256=?", (sha,))
        if not row or not os.path.isfile(row[0]):
            return None
        db, path = self.db, row[0]
        passport = self._passport(path)
        part = passport.get("part", "")
        with db._lock:
            if decision == CONFIRMED:
                path = self._move(path, "probable", CONFIRMED)
                passport.update(verdict=CONFIRMED, user_decision=CONFIRMED)
                by = list(passport.get("confirmed_by") or [])
                if "user" not in by:
                    by.append("user")
                passport["confirmed_by"] = by
                db.conn.execute("UPDATE acquisitions SET status=?, path=? WHERE sha256=?", (CONFIRMED, path, sha))
            else:
                path = self._move(path, "", REJECTED)
                passport.update(verdict=REJECTED, user_decision=REJECTED)
                fid = db.conn.execute("SELECT id FROM files WHERE sha256=?", (sha,)).fetchone()
                if fid:
                    db.conn.execute("DELETE FROM parts WHERE file_id=?", (fid[0],))
                db.conn.execute("DELETE FROM files WHERE sha256=?", (sha,))
                db.conn.execute("UPDATE acquisitions SET status=?, path='' WHERE sha256=?", (REJECTED, sha))
            db.conn.execute("INSERT OR REPLACE INTO user_decisions(sha256, part, domain, decision, at) "
                            "VALUES(?,?,?,?,?)", (sha, part, domain_of(passport.get("url", "")), decision, self.clock()))
            db.conn.commit()
        with io.open(path + ".json", "w", encoding="utf-8") as f:
            json.dump(passport, f, ensure_ascii=False, indent=2)
        return passport

    def _move(self, path: str, only_from: str, to: str) -> str:
        """Переносит файл с паспортом в `<library>/<to>/…`; `only_from` — откуда (пусто: откуда угодно)."""
        lib = self.db.library_dir
        rel = os.path.relpath(path, lib).replace("\\", "/")
        top, _, rest = rel.partition("/")
        if top == to or (only_from and top != only_from) or not rest:
            return path
        new = os.path.join(lib, to, *rest.split("/"))
        os.makedirs(os.path.dirname(new), exist_ok=True)
        os.replace(path, new)
        if os.path.exists(path + ".json"):
            os.replace(path + ".json", new + ".json")
        self.db.conn.execute("UPDATE files SET path=? WHERE path=?", (new, path))
        return new

    @staticmethod
    def _passport(path: str) -> Dict[str, Any]:
        try:
            with io.open(path + ".json", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # -------------------- доверие к доменам --------------------
    def counts(self, domain: str) -> Dict[str, int]:
        out = {CONFIRMED: 0, REJECTED: 0}
        for decision, n in self._all("SELECT decision, COUNT(*) FROM user_decisions WHERE domain=? GROUP BY decision",
                                     (domain.lower(),)):
            out[decision] = n
        return out

    def domains(self) -> Dict[str, Dict[str, int]]:
        return {d: self.counts(d) for (d,) in self._all("SELECT DISTINCT domain FROM user_decisions "
                                                        "WHERE domain<>'' ORDER BY domain")}

    @staticmethod
    def _is_bad(c: Dict[str, int]) -> bool:
        return c[REJECTED] >= BAD_MIN_REJECTS and c[REJECTED] >= 2 * c[CONFIRMED]

    @staticmethod
    def _is_good(c: Dict[str, int]) -> bool:
        return c[CONFIRMED] >= GOOD_MIN_CONFIRMS and c[REJECTED] == 0

    def bad_domains(self, makers=()) -> List[str]:
        return [d for d, c in self.domains().items() if self._is_bad(c) and d not in makers]

    def good_domains(self) -> List[str]:
        return [d for d, c in self.domains().items() if self._is_good(c)]

    def trust(self, base: SourceTrust) -> SourceTrust:
        """База доверия + выученное: плохие домены в `bad`, надёжные — в `catalogs` (не в `makers`)."""
        bad = list(dict.fromkeys(list(base.bad) + self.bad_domains(base.makers)))
        good = [d for d in self.good_domains() if d not in bad and d not in base.makers]
        return SourceTrust(makers=tuple(base.makers), catalogs=tuple(dict.fromkeys(list(base.catalogs) + good)),
                           bad=tuple(bad), maker_sites=dict(base.maker_sites))

    def forget_domain(self, domain: str) -> int:
        """Сбрасывает выученное про домен (решения по файлам остаются в паспортах)."""
        with self.db._lock:
            n = self.db.conn.execute("DELETE FROM user_decisions WHERE domain=?", (domain.lower(),)).rowcount
            self.db.conn.commit()
            return n

    # -------------------- sql --------------------
    def _one(self, sql: str, args: tuple = ()):
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> List[tuple]:
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchall()
