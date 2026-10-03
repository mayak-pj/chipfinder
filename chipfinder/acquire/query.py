# -*- coding: utf-8 -*-
"""План запросов на трёх языках (ARCHITECTURE §4.7).

`base_part` отбрасывает суффиксы корпуса и температуры, `family` даёт шаблон семейства,
`plan_queries` строит запросы en/zh/ru в порядке из конфига. Сети здесь нет.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

DEFAULT_ORDER = ("en", "zh", "ru")

# {part} — основа партномера, {code} — короткий код маркировки SMD
TEMPLATES = {
    "part": {
        "en": ["{part} datasheet pdf"],
        "zh": ["{part} 数据手册", "{part} 规格书 pdf", "{part} 中文资料"],
        "ru": ["{part} даташит", "{part} документация pdf"],
    },
    "smd": {
        "en": ["{code} smd marking code"],
        "zh": ["{code} 丝印"],
        "ru": ["{code} маркировка smd"],
    },
}

# Суффиксы корпуса/упаковки/температуры. Длинные — раньше коротких.
_SUFFIXES = ("SIQ", "SIG", "SNG", "SU", "SN", "DR", "TR", "DT", "PW", "DW", "T6", "T7", "U6")
_SINGLE = ("N", "P", "D")          # снимаются, только если перед ними цифра
_SMD_MAX_LEN = 4


def _core(raw: str) -> str:
    s = re.sub(r"\s+", "", (raw or "").upper())
    s = s.split("/")[0]                      # «/TR», «/NOPB»
    tokens = [t for t in s.split("-") if t]
    if not tokens:
        return ""
    core = tokens[0]
    if len(core) < 3 and len(tokens) > 1:    # «TL-431»: первый кусок слишком короткий
        core += tokens[1]
    return core


def base_part(raw: str) -> str:
    """AT24C02N-10SU-2.7 → AT24C02, W25Q64JVSIQ → W25Q64JV, LM358DR → LM358, PMS150C-U06 → PMS150C."""
    core = _core(raw)
    while True:
        for suf in _SUFFIXES:
            if core.endswith(suf) and len(core) - len(suf) >= 4:
                core = core[:-len(suf)]
                break
        else:
            for suf in _SINGLE:
                if core.endswith(suf) and len(core) > 4 and core[-2].isdigit():
                    core = core[:-1]
                    break
            else:
                return core


def family(raw: str) -> str:
    """STM32F103C8T6 → STM32F103, W25Q64JV → W25Q64; пусто, если отдельного семейства нет."""
    base = base_part(raw)
    fam = re.sub(r"(?<=\d)[A-Z]+\d*$", "", base)
    return fam if len(fam) >= 4 and fam != base else ""


def is_smd_code(raw: str) -> bool:
    """Короткая метка вроде «A6W»: ≤ 4 символа, есть и буква, и цифра."""
    s = re.sub(r"\s+", "", raw or "")
    return 0 < len(s) <= _SMD_MAX_LEN and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


@dataclass
class Query:
    lang: str
    text: str
    kind: str            # "part" | "family" | "smd"


def plan_queries(raw: str, langs: Optional[Sequence[str]] = None, smd: Optional[bool] = None,
                 templates: Optional[Dict[str, Dict[str, List[str]]]] = None) -> List[Query]:
    """Запросы по языкам в порядке `langs`. Короткий код (или smd=True) — запросы маркировки."""
    tpl = templates or TEMPLATES
    order = [l for l in (langs or DEFAULT_ORDER) if l in DEFAULT_ORDER] or list(DEFAULT_ORDER)
    as_smd = is_smd_code(raw) if smd is None else smd
    out: List[Query] = []
    seen = set()

    def add(lang: str, text: str, kind: str) -> None:
        if (lang, text) not in seen:
            seen.add((lang, text))
            out.append(Query(lang, text, kind))

    if as_smd:
        code = re.sub(r"\s+", "", raw).upper()
        for lang in order:
            for t in tpl["smd"].get(lang, []):
                add(lang, t.format(code=code), "smd")
        return out
    part = base_part(raw)
    if not part:
        return out
    fam = family(raw)
    for lang in order:
        for t in tpl["part"].get(lang, []):
            add(lang, t.format(part=part), "part")
        if fam:                                    # по одному запросу семейства на язык
            first = tpl["part"].get(lang, [])[:1]
            for t in first:
                add(lang, t.format(part=fam), "family")
    return out
