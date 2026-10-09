# -*- coding: utf-8 -*-
"""Сверка чипа с фото и найденного datasheet: партномер, маркировка, корпус, производитель."""
from __future__ import annotations

import re
from typing import List, Tuple

from ..core.interfaces import Comparator
from ..core.models import CheckItem, ChipInfo, Comparison
from ..core.utils import norm_part
from .identify_rules import MAKER_WORDS, _is_date_code

PKG_RE = re.compile(
    r"\b(P?DIP|SPDIP|SOIC|SOP|SO|SSOP|TSSOP|HTSSOP|MSOP|VSSOP|QSOP|SOT|SC|TO|D2PAK|DPAK|"
    r"QFN|VQFN|WQFN|UQFN|HVQFN|DFN|UDFN|WDFN|TDFN|XDFN|SON|WSON|USON|VSON|X2SON|"
    r"LQFP|TQFP|PQFP|QFP|LGA|BGA|FBGA|TFBGA|VFBGA|TSOP|TSOPII|PLCC|WLCSP|CSP|ESOP|HSOP|SOP8|MFP)"
    r"(?:[-\s]?(\d{1,3}))?(?:[-\s/](\d{1,3}))?(?:L)?\b")  # регистр важен: "to 3 V" — не корпус TO-3

SOT_DEFAULT = {("SOT", "23"): 3, ("SOT", "89"): 3, ("SOT", "223"): 4, ("SOT", "143"): 4, ("SOT", "323"): 3,
               ("SOT", "363"): 6, ("SOT", "353"): 5, ("SOT", "563"): 6, ("SOT", "553"): 5, ("SC", "70"): 3,
               ("SC", "88"): 6, ("TO", "92"): 3, ("TO", "220"): 3, ("TO", "252"): 3, ("TO", "263"): 3,
               ("TO", "247"): 3, ("TO", "126"): 3, ("TO", "3"): 2, ("TO", "18"): 3, ("TO", "5"): 3, ("TO", "39"): 3}

FAMILY_ALIAS = {"SO": "SOP", "SOIC": "SOP", "SOP": "SOP", "ESOP": "SOP", "HSOP": "SOP", "MFP": "SOP",
                "PDIP": "DIP", "DIP": "DIP", "SPDIP": "DIP",
                "VQFN": "QFN", "WQFN": "QFN", "UQFN": "QFN", "HVQFN": "QFN", "QFN": "QFN",
                "UDFN": "DFN", "WDFN": "DFN", "TDFN": "DFN", "XDFN": "DFN", "DFN": "DFN",
                "SON": "DFN", "WSON": "DFN", "USON": "DFN", "VSON": "DFN", "X2SON": "DFN"}


def parse_packages(text: str) -> List[Tuple[str, int]]:
    """[('SOP-8', 8), ('SOT-23-5', 5), ...] из текста."""
    found = {}
    for m in PKG_RE.finditer(text or ""):
        fam = m.group(1).upper()
        a, b = m.group(2), m.group(3)
        if fam in ("SOT", "SC", "TO"):
            if not a:
                continue
            pins = int(b) if b and int(b) <= 8 else SOT_DEFAULT.get((fam, a), 0)
            name = "%s-%s%s" % (fam, a, ("-" + b) if b and int(b) <= 8 else "")
        else:
            if fam == "SOP8":
                fam, a = "SOP", "8"
            if not a:
                continue
            pins = int(a)
            if not (3 <= pins <= 1200):
                continue
            name = "%s-%d" % (fam, pins)
        found[name] = pins
    for m in re.finditer(r"\b(\d{1,3})[-\s](?:pin|lead|ball)s?\b", text or "", re.I):
        n = int(m.group(1))
        if 3 <= n <= 1200:
            found.setdefault("%d выводов" % n, n)
    return sorted(found.items(), key=lambda kv: kv[1])


def pkg_family(name: str) -> str:
    fam = re.match(r"[A-Za-z0-9]+?(?=[-\s]?\d|$)", name or "")
    f = (fam.group(0) if fam else name or "").upper()
    return FAMILY_ALIAS.get(f, f)


class BasicComparator(Comparator):
    name = "basic"

    def compare(self, part: str, marking: str, chip: ChipInfo, datasheet_text: str) -> Comparison:
        cmp = Comparison()
        text = datasheet_text or ""
        nt = norm_part(text)
        p = norm_part(part)
        weights = []

        # 1. Партномер
        if not text.strip():
            cmp.checks.append(CheckItem("Текст datasheet", "fail",
                                        "Текст из PDF не извлекается (скан?). Сверка невозможна — проверьте вручную."))
            cmp.verdict = "Нельзя сверить автоматически"
            return cmp
        if p and p in nt:
            cmp.checks.append(CheckItem("Партномер в datasheet", "ok", "%s найден в тексте" % part))
            weights.append((1.0, 3))
        else:
            fam = ""
            for k in range(len(p) - 1, max(3, int(len(p) * 0.6)) - 1, -1):
                if p[:k] in nt:
                    fam = p[:k]
                    break
            if fam:
                cmp.checks.append(CheckItem("Партномер в datasheet", "warn",
                                            "Найдено только начало «%s» — возможно, datasheet на семейство. "
                                            "Проверьте таблицу заказных кодов (Ordering information)." % fam))
                weights.append((0.6, 3))
            else:
                cmp.checks.append(CheckItem("Партномер в datasheet", "fail", "%s в тексте не найден" % part))
                weights.append((0.0, 3))

        # 2. Маркировка
        toks = []
        for t in re.split(r"\s+", marking or ""):
            n = norm_part(t)
            if len(n) >= 3 and not _is_date_code(n) and n not in MAKER_WORDS and n != p:
                toks.append(n)
        if toks:
            found = [t for t in toks if t in nt]
            mark_zone = bool(re.search(r"marking|top\s*mark|device\s*mark|丝印|маркировк", text, re.I))
            if found:
                cmp.checks.append(CheckItem("Маркировка с фото", "ok",
                                            "В datasheet есть: %s%s" % (", ".join(found),
                                                                         " (раздел о маркировке присутствует)" if mark_zone else "")))
                weights.append((1.0, 2))
            else:
                cmp.checks.append(CheckItem("Маркировка с фото", "warn" if mark_zone else "unknown",
                                            "Строки %s в тексте не найдены (часто это код даты/партии — это нормально)"
                                            % ", ".join(toks)))
                weights.append((0.4, 1))

        # 3. Корпус и число выводов
        pkgs = parse_packages(text)
        cmp.packages_in_datasheet = [n for n, _ in pkgs]
        pin_set = sorted(set(pins for _, pins in pkgs))
        if not pkgs:
            cmp.checks.append(CheckItem("Корпус", "unknown", "Корпуса в тексте не распознаны"))
        elif chip.package and not chip.package.startswith("выводы"):
            fam = pkg_family(chip.package)
            same_fam = [n for n, _ in pkgs if pkg_family(n) == fam]
            same_full = [n for n, pn in pkgs if pkg_family(n) == fam and (not chip.pins or pn == chip.pins)]
            if same_full:
                cmp.checks.append(CheckItem("Корпус", "ok", "%s есть в datasheet (%s)" % (chip.package, ", ".join(same_full))))
                weights.append((1.0, 2))
            elif same_fam:
                cmp.checks.append(CheckItem("Корпус", "warn", "Тип %s есть, но с другим числом выводов: %s"
                                            % (fam, ", ".join(same_fam))))
                weights.append((0.4, 2))
            else:
                cmp.checks.append(CheckItem("Корпус", "fail", "%s нет в datasheet. Есть: %s"
                                            % (chip.package, ", ".join(cmp.packages_in_datasheet[:12]))))
                weights.append((0.0, 2))
        elif chip.pins:
            if chip.pins in pin_set:
                cmp.checks.append(CheckItem("Число выводов", "ok" if not chip.pins_estimated else "warn",
                                            "%d выводов%s — есть такой корпус: %s" % (
                                                chip.pins, " (оценка по фото)" if chip.pins_estimated else "",
                                                ", ".join(n for n, pn in pkgs if pn == chip.pins))))
                weights.append((1.0 if not chip.pins_estimated else 0.8, 2 if not chip.pins_estimated else 1))
            else:
                cmp.checks.append(CheckItem("Число выводов", "fail" if not chip.pins_estimated else "warn",
                                            "%d выводов%s, а в datasheet: %s" % (
                                                chip.pins, " (оценка по фото, может быть неточной)" if chip.pins_estimated else "",
                                                ", ".join(cmp.packages_in_datasheet[:12]))))
                weights.append((0.0 if not chip.pins_estimated else 0.4, 2 if not chip.pins_estimated else 1))
        else:
            cmp.checks.append(CheckItem("Корпус", "unknown", "Укажите корпус/выводы на фото. В datasheet: %s"
                                        % ", ".join(cmp.packages_in_datasheet[:12])))

        # 4. Производитель
        makers_photo = set(MAKER_WORDS[n] for n in (norm_part(t) for t in (marking or "").split()) if n in MAKER_WORDS)
        if makers_photo:
            head = text[:5000].lower()
            ok = [m for m in makers_photo if m.split()[0].lower() in head]
            cmp.checks.append(CheckItem("Производитель", "ok" if ok else "warn",
                                        ("%s упомянут в datasheet" % ", ".join(ok)) if ok else
                                        "На чипе %s, в начале datasheet не упомянут (возможен аналог/второй источник)"
                                        % ", ".join(makers_photo)))
            weights.append((1.0 if ok else 0.5, 1))

        total = sum(w for _, w in weights) or 1
        cmp.score = round(sum(s * w for s, w in weights) / total, 2)
        if cmp.score >= 0.8:
            cmp.verdict = "Документ соответствует чипу"
        elif cmp.score >= 0.5:
            cmp.verdict = "Вероятно соответствует — проверьте отмеченные пункты"
        else:
            cmp.verdict = "Скорее НЕ соответствует"
        return cmp
