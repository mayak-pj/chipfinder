# -*- coding: utf-8 -*-
"""Список сайтов для администраторов (ARCHITECTURE §4.11; шаг 5.6): таблица `network_blocks`.

Копится постоянно: домен, класс, первое и последнее появление, число попыток, партномера, пример URL, ожидаемая
польза (уровень источника и его успех по статистике), статус `blocked` / `open`. Фоновой перепроверки нет: статус
«доступ открыт» ставится, когда домен ответил при обычном поиске (`record_ok`) или в «Диагностике»
(`apply_diagnosis`). В список попадает только `network_blocked`; решает классификатор (`netdiag.BlockTracker`).
Время — через `clock`; записи — короткие транзакции под блокировкой `SQLiteLocalDB`, как в `cache.py`.
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from .netdiag import NETWORK_BLOCKED

BLOCKED = "blocked"
OPEN = "open"
MAX_PARTS = 50
SCHEMA = """
CREATE TABLE IF NOT EXISTS network_blocks(
  domain TEXT PRIMARY KEY, cls TEXT, first_at REAL, last_at REAL, attempts INTEGER DEFAULT 0, parts TEXT DEFAULT '',
  sample_url TEXT DEFAULT '', level TEXT DEFAULT '', benefit REAL, status TEXT DEFAULT 'blocked', opened_at REAL);
"""
CSV_COLUMNS = ("domain", "port", "status", "attempts", "parts", "first_at", "last_at", "sample_url", "level", "benefit")


def _stamp(t: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""


class AccessLog:
    def __init__(self, db, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        with db._lock:
            db.conn.executescript(SCHEMA)
            db.conn.commit()

    def _run(self, sql: str, args: tuple = ()) -> int:
        with self.db._lock:
            cur = self.db.conn.execute(sql, args)
            self.db.conn.commit()
            return cur.rowcount

    def _all(self, sql: str, args: tuple = ()) -> List[tuple]:
        with self.db._lock:
            return self.db.conn.execute(sql, args).fetchall()

    # -------------------- запись --------------------
    def record_failure(self, domain: str, cls: str, part: str = "", url: str = "", level: str = "",
                       benefit: Optional[float] = None) -> bool:
        """Неудача доступа. Учитывается только `network_blocked`; возвращает, учтена ли."""
        if cls != NETWORK_BLOCKED or not domain:
            return False
        d, now = domain.lower(), self.clock()
        row = self._all("SELECT parts, sample_url, level, benefit FROM network_blocks WHERE domain=?", (d,))
        if row:
            parts = set(p for p in row[0][0].split("\n") if p)
            if part:
                parts.add(part)
            self._run("UPDATE network_blocks SET cls=?, last_at=?, attempts=attempts+1, parts=?, sample_url=?, "
                      "level=?, benefit=?, status=?, opened_at=NULL WHERE domain=?",
                      (cls, now, "\n".join(sorted(parts)[:MAX_PARTS]), row[0][1] or url, level or row[0][2],
                       benefit if benefit is not None else row[0][3], BLOCKED, d))
        else:
            self._run("INSERT INTO network_blocks(domain, cls, first_at, last_at, attempts, parts, sample_url, level, "
                      "benefit, status) VALUES(?,?,?,?,1,?,?,?,?,?)",
                      (d, cls, now, now, part, url, level, benefit, BLOCKED))
        return True

    def record_ok(self, domain: str) -> bool:
        """Домен ответил: если был в списке как заблокированный — «доступ открыт». Возвращает, сменился ли статус."""
        return self._run("UPDATE network_blocks SET status=?, opened_at=? WHERE domain=? AND status=?",
                         (OPEN, self.clock(), domain.lower(), BLOCKED)) > 0

    def apply_diagnosis(self, verdicts: Mapping[str, str]) -> None:
        """Итог «Диагностики»: домен → вердикт (`ok` открывает доступ; остальное статус не меняет)."""
        for domain, verdict in verdicts.items():
            if verdict == "ok":
                self.record_ok(domain)

    # -------------------- чтение --------------------
    @staticmethod
    def _row(r: tuple) -> Dict[str, Any]:
        return {"domain": r[0], "cls": r[1], "first_at": r[2], "last_at": r[3], "attempts": r[4],
                "parts": [p for p in r[5].split("\n") if p], "sample_url": r[6], "level": r[7], "benefit": r[8],
                "status": r[9], "opened_at": r[10]}

    def entries(self, status: str = "") -> List[Dict[str, Any]]:
        where, args = ("WHERE status=?", (status,)) if status else ("", ())
        return [self._row(r) for r in self._all(
            "SELECT domain, cls, first_at, last_at, attempts, parts, sample_url, level, benefit, status, opened_at "
            "FROM network_blocks %s ORDER BY attempts DESC, domain" % where, args)]

    def get(self, domain: str) -> Optional[Dict[str, Any]]:
        rows = [r for r in self.entries() if r["domain"] == domain.lower()]
        return rows[0] if rows else None

    # -------------------- экспорт «Запрос на доступ» --------------------
    def export_csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        for r in self.entries(BLOCKED):
            w.writerow([r["domain"], 443, r["status"], r["attempts"], "; ".join(r["parts"]), _stamp(r["first_at"]),
                        _stamp(r["last_at"]), r["sample_url"], r["level"],
                        "" if r["benefit"] is None else "%.2f" % r["benefit"]])
        return out.getvalue()

    def export_txt(self, log_hint: str = "") -> str:
        rows = self.entries(BLOCKED)
        lines = ["Запрос на доступ к сайтам (ChipFinder)", "",
                 "Программа ищет и скачивает документацию (PDF datasheet) на микросхемы. Что делает программа: "
                 "только чтение страниц и скачивание PDF; ничего не отправляет на сайты и не устанавливает.",
                 "Прошу открыть доступ по HTTPS (порт 443) к следующим сайтам:", ""]
        if not rows:
            lines.append("Заблокированных сайтов нет.")
        for i, r in enumerate(rows, 1):
            lines.append("%d. %s (порт 443)" % (i, r["domain"]))
            lines.append("   нужен %d раз(а), с %s по %s" % (r["attempts"], _stamp(r["first_at"]), _stamp(r["last_at"])))
            if r["parts"]:
                lines.append("   для микросхем: %s" % ", ".join(r["parts"]))
            if r["sample_url"]:
                lines.append("   пример: %s" % r["sample_url"])
            if r["benefit"] is not None:
                lines.append("   ожидаемая польза: %d%% успешных поисков%s"
                             % (round(r["benefit"] * 100), " (уровень %s)" % r["level"] if r["level"] else ""))
        if log_hint:
            lines += ["", "Журнал обращений программы: %s" % log_hint]
        return "\n".join(lines) + "\n"
