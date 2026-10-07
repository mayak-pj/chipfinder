# -*- coding: utf-8 -*-
"""Данные живой ленты поиска (ARCHITECTURE §4.8, §5): история событий по уровням и счётчики.

Строка истории — событие шины: время, значок итога, метка языка, текст на языке поиска; русский перевод —
в подсказке. Событие «идёт…» занимает строку, пока не придёт его итог от того же источника, — итог встаёт
на её место. Вид и отрисовка — `gui/search_feed.py`.
"""
from __future__ import annotations

import io
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PyQt5.QtCore import QAbstractListModel, QModelIndex, Qt

from ..acquire.events import LANG_LABELS, Event, EventCounters, render

KIND_ROLE, EVENT_ROLE, STATE_ROLE, LANG_ROLE, TIME_ROLE, TEXT_ROLE, COUNT_ROLE = (Qt.UserRole + i for i in range(1, 8))
MAX_EVENTS = 500          # строк истории; старые уходят
# состояние строки: иконка, цвет (имя токена темы), значок для текста и журнала
STATES = {"pending": ("clock", "accent", u"⏳"),
          "ok": ("check", "success", u"✔"),
          "found": ("check", "success", u"✔"),
          "empty": ("minus", "text_muted", u"·"),
          "fail": ("x", "danger", u"✘"),
          "skip": ("skip-forward", "warning", u"⤼"),
          "info": ("info", "text_muted", u"·"),
          "stale": ("skip-forward", "text_disabled", u"⤼")}       # «идёт…», оставшееся без итога после остановки
# группы истории, которых нет среди уровней sources.json
GROUP_TITLES = {"ocr": u"Распознавание", "start": u"Поиск", "local": u"Локальная база", "result": u"Итог",
                "other": u"Прочее"}
_NUMBER = re.compile(r"^\s*\d+\.\s*")


def level_names(sources_path: str) -> Dict[str, str]:
    """Названия уровней из sources.json без номеров («1. Сайты-каталоги» → «Сайты-каталоги»). Диск — только из фона."""
    try:
        with io.open(sources_path, "r", encoding="utf-8") as f:
            levels = json.load(f).get("levels") or []
        return {str(lv["id"]): _NUMBER.sub("", str(lv.get("name") or lv["id"])) for lv in levels}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def group_of(event: Event, current: str = "") -> str:
    """Группа истории для события: уровень источника; у событий без уровня — по типу или текущая."""
    if event.kind in ("ocr", "photo"):
        return "ocr"
    if event.kind in ("result", "error") or event.kind == "search" and event.final:
        return "result"
    if event.kind == "search":
        return "start"
    return event.level or ("local" if event.kind == "local" else current or "other")


def state_of(event: Event) -> str:
    if event.final:
        return event.outcome
    return "info" if event.kind == "search" else "pending"


class FeedRow(object):
    """Строка истории: событие и всё, что нужно для показа (текст собирается один раз, не при отрисовке)."""
    __slots__ = ("event", "state", "stamp", "lang", "text", "tip")

    def __init__(self, event: Event):
        self.event = event
        self.state = state_of(event)
        self.stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
        self.lang = LANG_LABELS.get(event.display_lang, event.display_lang.upper()[:4])
        self.text = render(event)
        self.tip = self.text if event.display_lang == "ru" else render(event, "ru")

    def line(self) -> str:
        return u"%s %s %s %s" % (self.stamp, STATES[self.state][2], self.lang, self.text)


class FeedModel(QAbstractListModel):
    """История поиска списком: строка-заголовок уровня, под ней события этого уровня по порядку."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.counters = EventCounters()
        self._names: Dict[str, str] = {}
        self._groups: List[Tuple[str, List[FeedRow]]] = []        # в порядке появления
        self._flat: List[Tuple[str, Optional[FeedRow]]] = []      # строки списка: (группа, None) — заголовок
        self._current = ""                                        # уровень, в котором идёт поиск

    # ---------- изменение ----------
    def set_level_names(self, names: Dict[str, str]) -> None:
        self.beginResetModel()
        self._names = {k: _NUMBER.sub("", v) for k, v in (names or {}).items()}
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._groups, self._flat, self._current = [], [], ""
        self.counters.reset()
        self.endResetModel()

    def add(self, events: Iterable[Event]) -> int:
        """Пачка событий — одним обновлением списка. Возвращает строку последнего события (-1 — пачка пуста)."""
        last = None
        self.beginResetModel()
        for event in events:
            self.counters.add(event)
            last = self._place(FeedRow(event))
        self._trim()
        self._rebuild()
        self.endResetModel()
        return next((i for i, (_g, row) in enumerate(self._flat) if row is last), -1) if last else -1

    def finish(self) -> None:
        """Задача кончилась или остановлена: строки «идёт…» без итога больше не идут."""
        stale = [i for i, (_g, row) in enumerate(self._flat) if row is not None and row.state == "pending"]
        for i in stale:
            self._flat[i][1].state = "stale"
            self.dataChanged.emit(self.index(i), self.index(i))

    def _place(self, row: FeedRow) -> FeedRow:
        e = row.event
        gid = group_of(e, self._current)
        if e.level or e.kind == "local":
            self._current = gid
        rows = next((r for g, r in self._groups if g == gid), None)
        if rows is None:
            rows = []
            self._groups.append((gid, rows))
        for i in range(len(rows) - 1, -1, -1):            # итог (или ход) — на место своей строки «идёт…»
            old = rows[i]
            if old.state == "pending" and old.event.source == e.source and old.event.kind == e.kind:
                rows[i] = row
                return row
        rows.append(row)
        return row

    def _trim(self) -> None:
        extra = sum(len(r) for _g, r in self._groups) - MAX_EVENTS
        while extra > 0 and self._groups:
            rows = self._groups[0][1]
            cut = min(extra, len(rows))
            del rows[:cut]
            extra -= cut
            if not rows:
                del self._groups[0]

    def _rebuild(self) -> None:
        self._flat = []
        for gid, rows in self._groups:
            self._flat.append((gid, None))
            self._flat += [(gid, row) for row in rows]

    # ---------- чтение ----------
    def groups(self) -> List[Tuple[str, int]]:
        return [(gid, len(rows)) for gid, rows in self._groups]

    def title(self, gid: str) -> str:
        return GROUP_TITLES.get(gid) or self._names.get(gid) or gid

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._flat)

    def flags(self, index):
        return Qt.ItemIsEnabled if index.isValid() else Qt.NoItemFlags

    def data(self, index, role=Qt.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._flat):
            return None
        gid, row = self._flat[index.row()]
        if row is None:
            if role == KIND_ROLE:
                return "group"
            if role == Qt.DisplayRole:
                return self.title(gid)
            if role == COUNT_ROLE:
                return next((len(r) for g, r in self._groups if g == gid), 0)
            return None
        if role == Qt.DisplayRole:
            return row.line()
        if role == Qt.ToolTipRole:
            return row.tip
        return {KIND_ROLE: "event", EVENT_ROLE: row.event, STATE_ROLE: row.state, LANG_ROLE: row.lang,
                TIME_ROLE: row.stamp, TEXT_ROLE: row.text}.get(role)
