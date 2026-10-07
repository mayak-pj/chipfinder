# -*- coding: utf-8 -*-
"""Адаптивный порядок источников (ARCHITECTURE §4.10; шаг 6.3): кто чаще и быстрее подтверждает — тот раньше.

`AdaptiveOrder.plan(уровни, партномер)` перед поиском отдаёт порядок уровней и источников внутри уровня:

* польза источника = P / (время + штраф за запросы). P — сглаженная доля поисков с подтверждённым документом:
  у источника как будто уже есть `virtual` попыток с долей из порядка по умолчанию (первый в `sources.json` —
  самый полезный), история семейства чипа так же сглаживается общей историей источника. Время — среднее до
  подтверждения, запросы — среднее на поиск; без истории оба равны у всех, и порядок остаётся порядком по умолчанию;
* уровень стоит там, где его лучший источник;
* пока поисков с обращением к источникам меньше `min_searches` или режим выключен — порядок по умолчанию;
* разведка: в доле поисков `explore` первым идёт источник, у которого попыток меньше `virtual` (самый редкий).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Sequence, Tuple

from .query import base_part, family as part_family

MIN_SEARCHES = 20         # после скольких поисков включается адаптивный порядок
EXPLORE = 0.1             # доля поисков с разведкой
VIRTUAL = 5               # «виртуальные» попытки: вес порядка по умолчанию против истории
PRIOR = (0.6, 0.1)        # доля успеха «по умолчанию» у первого и у последнего источника
BASE_SECONDS = 30.0       # время до подтверждения, пока своих успехов нет
QUERY_SECONDS = 1.5       # штраф за запрос: пауза между обращениями к одному сайту


@dataclass
class OrderPlan:
    adaptive: bool                # порядок изменён по статистике (иначе — по умолчанию)
    searches: int                 # по скольким поискам
    levels: List[str]
    sources: Dict[str, List[str]]
    explored: str = ""            # источник, поставленный первым для разведки
    utility: Dict[str, float] = field(default_factory=dict)


class AdaptiveOrder:
    def __init__(self, stats: Any, enabled: bool = True, min_searches: int = MIN_SEARCHES, explore: float = EXPLORE,
                 virtual: int = VIRTUAL, rng: Callable[[], float] = random.random) -> None:
        self.stats = stats
        self.enabled = bool(enabled)
        self.min_searches = int(min_searches)
        self.explore = float(explore)
        self.virtual = max(1, int(virtual))
        self.rng = rng

    def plan(self, levels: Sequence[Tuple[str, Sequence[str]]], part: str = "") -> OrderPlan:
        """`levels` — [(уровень, [id источников])] в порядке по умолчанию."""
        default = OrderPlan(False, 0, [lv for lv, _ in levels], {lv: list(ids) for lv, ids in levels})
        if not self.enabled:
            return default
        default.searches = searches = self.stats.network_searches()
        flat = [(lv, sid) for lv, ids in levels for sid in ids]
        if searches < self.min_searches or len(flat) < 2:
            return default
        overall = self.stats.totals()
        fam = self.stats.totals(part_family(part) or base_part(part)) if part else {}
        utility = {sid: self._utility(i, len(flat), overall.get(sid), fam.get(sid))
                   for i, (_, sid) in enumerate(flat)}
        place = {sid: i for i, (_, sid) in enumerate(flat)}
        sources = {lv: sorted(ids, key=lambda s: (-utility[s], place[s])) for lv, ids in levels}
        order = sorted((lv for lv, _ in levels),
                       key=lambda lv: -max([utility[s] for s in sources[lv]] or [0.0]))    # сортировка устойчивая
        plan = OrderPlan(True, searches, order, sources, utility=utility)
        if self.explore > 0 and self.rng() < self.explore:
            rare = [(overall.get(sid, {}).get("attempts", 0), place[sid], lv, sid) for lv, sid in flat]
            attempts, _, level, sid = min(rare)
            if attempts < self.virtual:
                plan.explored = sid
                plan.levels = [level] + [lv for lv in order if lv != level]
                sources[level] = [sid] + [s for s in sources[level] if s != sid]
        return plan

    def _utility(self, index: int, count: int, overall: Any, fam: Any) -> float:
        v = float(self.virtual)
        first, last = PRIOR
        prior = first - (first - last) * index / max(1, count - 1)
        o = overall or {"attempts": 0, "wins": 0, "queries": 0, "seconds": 0.0}
        p = (o["wins"] + v * prior) / (o["attempts"] + v)
        if fam:
            p = (fam["wins"] + v * p) / (fam["attempts"] + v)
        seconds = (o["seconds"] + v * BASE_SECONDS) / (o["wins"] + v)
        queries = (o["queries"] + v) / (o["attempts"] + v)
        return p / (seconds + QUERY_SECONDS * queries)
