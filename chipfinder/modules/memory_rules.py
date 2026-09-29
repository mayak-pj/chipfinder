# -*- coding: utf-8 -*-
"""Заключение: есть ли в микросхеме память и какая.

Два источника сведений:
  1. Справочник семейств по партномеру (data/part_rules.json) и описание из каталога.
  2. Текст datasheet: ключевые слова (EEPROM, Flash, OTP, EPROM, PROM, SRAM,
     DRAM, FRAM, NVRAM...) и объёмы рядом с ними.
"""
from __future__ import annotations

import re
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Tuple

from ..core.interfaces import MemoryAnalyzer
from ..core.models import MemoryItem, MemoryVerdict
from ..core.partrules import get_rules

KINDS: "OrderedDict[str, str]" = OrderedDict([
    ("EEPROM", r"\bE2PROM\b|\bE²PROM\b|\bEEPROM\b|electrically[- ]erasable|Data EEPROM"),
    ("Flash", r"\b(?:NOR|NAND|serial|SPI|QSPI|embedded|on-chip|program|internal)\s+flash\b|"
              r"\bflash\s+(?:memory|program|ROM|array)\b|\d+\s*[KkMm](?:B|bytes?|bits?|b)?\s+(?:of\s+)?flash\b|"
              r"\bflash\s*:\s*\d+"),
    ("OTP", r"\bOTP\b(?!\s*trim)|one[- ]time[- ]programmable"),
    ("EPROM", r"(?<!E)\bEPROM\b|UV[- ]erasable"),
    ("PROM", r"(?<![EP])\bPROM\b|fusible[- ]link|fuse[- ]programmable"),
    ("Mask ROM", r"\bmask(?:ed)?[- ]ROM\b"),
    ("FRAM", r"\bF-?RAM\b|ferroelectric"),
    ("NVRAM", r"\bNVRAM\b|\bnvSRAM\b|non-?volatile\s+(?:static\s+)?RAM|battery[- ]backed\s+(?:S)?RAM"),
    ("SRAM", r"\bSRAM\b|static\s+RAM|\bdata\s+RAM\b|\bRAM\s*:\s*\d+|\d+\s*[KkMm]?(?:B|bytes?)\s+(?:of\s+)?(?:S)?RAM\b"),
    ("DRAM", r"\bS?DRAM\b|\bLPDDR\d?\b|\bDDR[2-5]?L?\s+SDRAM\b|\bDDR[2-5]L?\b"),
])

SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([KMG])?\s*[- ]?(bits?|bytes?|Byte|B|b|bit)\b(?!\s*/)"
                     r"|(\d+)\s*[KM]?\s*[x×]\s*(8|16|32)\b", re.I)

KIND_RU = {"EEPROM": "EEPROM (электрически стираемая ПЗУ)", "Flash": "Flash",
           "OTP": "OTP (однократно программируемая)", "EPROM": "EPROM (стирание ультрафиолетом)",
           "PROM": "PROM (однократно программируемая, пережигаемые перемычки)", "Mask ROM": "Масочное ПЗУ",
           "FRAM": "FRAM (сегнетоэлектрическая, энергонезависимая)", "NVRAM": "NVRAM / nvSRAM",
           "SRAM": "SRAM / RAM (энергозависимая)", "DRAM": "DRAM (энергозависимая)"}
NONVOLATILE = {"EEPROM", "Flash", "OTP", "EPROM", "PROM", "Mask ROM", "FRAM", "NVRAM"}


def _size_near(text: str, start: int, end: int) -> str:
    win = text[max(0, start - 60): min(len(text), end + 60)]
    best, best_dist = "", 10 ** 9
    center = start - max(0, start - 60)
    for m in SIZE_RE.finditer(win):
        if m.group(1):
            num, mult, unit = m.group(1), (m.group(2) or "").upper(), m.group(3)
            if unit in ("b", "bit", "bits") or unit.lower().startswith("bit"):
                u = "bit"
            else:
                u = "B"
            if not mult and float(num.replace(",", ".")) < 16 and u == "bit":
                continue  # "8 bit" — разрядность, а не объём
            s = "%s %s%s" % (num, mult, "bit" if u == "bit" else "B")
        else:
            s = "%s x %s" % (m.group(4), m.group(5))
        d = abs(m.start() - center)
        if d < best_dist:
            best, best_dist = s, d
    return best


def _same(kind: str, label: str) -> bool:
    return re.search(r"(^|[^a-z])" + re.escape(kind.lower()), label.lower()) is not None


def _snippet(text: str, start: int, end: int) -> str:
    s = text[max(0, start - 50): min(len(text), end + 50)]
    return "…" + re.sub(r"\s+", " ", s).strip() + "…"


class RuleMemoryAnalyzer(MemoryAnalyzer):
    name = "rules"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        self.rules = get_rules(ctx.app_dir)

    def _from_text(self, text: str) -> Dict[str, Tuple[int, str, str, bool]]:
        """kind -> (кол-во упоминаний, размер, цитата, упомянуто_в_начале)"""
        res = {}
        head_len = 6000
        for kind, pat in KINDS.items():
            ms = list(re.finditer(pat, text, re.I if kind not in ("OTP", "PROM", "EPROM") else 0))
            if not ms:
                continue
            sizes = Counter()
            for m in ms[:40]:
                sz = _size_near(text, m.start(), m.end())
                if sz:
                    sizes[sz] += 1
            size = sizes.most_common(1)[0][0] if sizes else ""
            first = ms[0]
            res[kind] = (len(ms), size, _snippet(text, first.start(), first.end()), first.start() < head_len)
        return res

    def analyze(self, part: str, datasheet_text: str, description: str = "") -> MemoryVerdict:
        v = MemoryVerdict(has_memory=None)
        rule = self.rules.match(part) if part else None
        items: "OrderedDict[str, MemoryItem]" = OrderedDict()
        notes = []

        if rule is not None:
            if rule["memory"]:
                notes.append("Семейство: %s" % rule["family"])
                for m in rule["memory"]:
                    items[m["kind"]] = MemoryItem(kind=m["kind"], size=m.get("size", ""),
                                                  evidence="справочник семейств (%s)" % rule["family"])

        desc = description or ""
        if desc:
            for kind, pat in KINDS.items():
                if re.search(pat, desc, re.I) and not any(_same(kind, k) for k in items):
                    items[kind] = MemoryItem(kind=KIND_RU.get(kind, kind), evidence="описание в каталоге: " + desc[:120])

        text = datasheet_text or ""
        tx = self._from_text(text) if text.strip() else {}
        strong = {}
        for kind, (cnt, size, snip, early) in tx.items():
            # OTP в аналоговых чипах часто означает биты заводской подстройки
            if kind == "OTP" and re.search(r"trim|calibrat|подстро", snip, re.I) and cnt < 3:
                notes.append("Упоминание OTP похоже на биты заводской подстройки, не на память пользователя")
                continue
            if cnt >= 2 or early:
                strong[kind] = (cnt, size, snip)
        for kind, (cnt, size, snip) in strong.items():
            key = next((k for k in items if _same(kind, k)), None)
            if key:
                it = items[key]
                if size and not it.size:
                    it.size = size
                it.evidence += "; datasheet: %d упоминаний %s" % (cnt, snip)
            else:
                items[kind] = MemoryItem(kind=KIND_RU.get(kind, kind), size=size,
                                         evidence="datasheet (%d упом.): %s" % (cnt, snip))

        v.items = list(items.values())
        if v.items:
            v.has_memory = True
            v.confidence = 0.9 if (rule and rule["memory"] and strong) else (0.75 if strong else 0.6)
            nv = [i.kind for i in v.items if any(k.lower() in i.kind.lower() for k in NONVOLATILE)
                  or "энергонезав" in i.kind.lower() or "OTP" in i.kind or "ПЗУ" in i.kind]
            vol = [i.kind for i in v.items if i.kind not in nv]
            parts = []
            if nv:
                parts.append("энергонезависимая (сохраняет данные без питания): " + "; ".join(nv))
            if vol:
                parts.append("энергозависимая: " + "; ".join(vol))
            v.summary = "ЕСТЬ ПАМЯТЬ — " + " | ".join(parts)
        elif rule is not None and rule["memory"] == []:
            v.has_memory = False
            v.confidence = 0.8 if text.strip() else 0.55
            v.summary = "Памяти нет (семейство «%s»%s)" % (rule["family"],
                                                           ", в datasheet упоминаний памяти не найдено" if text.strip() else "")
        elif text.strip():
            v.has_memory = False
            v.confidence = 0.5
            v.summary = "В datasheet упоминаний памяти не найдено — вероятно, памяти нет"
        else:
            v.summary = "Неизвестно: нет datasheet и партномер не найден в справочнике"
        if notes:
            v.summary += ". " + ". ".join(notes)
        return v
