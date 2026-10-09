# -*- coding: utf-8 -*-
"""Шрифты окна: основной + китайский запасной (строка состояния поиска бывает на 中文). Выбор — без Qt."""
from __future__ import annotations

from typing import Iterable, List

# по порядку предпочтения; первый найденный в системе
UI_FONTS = ["Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "Tahoma", "Arial"]
CJK_FONTS = ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
             "Source Han Sans SC", "WenQuanYi Micro Hei", "SimHei", "SimSun", "Arial Unicode MS"]


def pick(available: Iterable[str], wanted: List[str]) -> str:
    """Первый шрифт из `wanted`, который есть в системе (без учёта регистра); нет ни одного → ''."""
    have = {a.lower(): a for a in available}
    for name in wanted:
        if name.lower() in have:
            return have[name.lower()]
    return ""


def families(available: Iterable[str], system_default: str = "") -> List[str]:
    """Цепочка семейств для окна: основной шрифт (или системный), затем китайский."""
    available = list(available)
    out = [pick(available, UI_FONTS) or system_default, pick(available, CJK_FONTS)]
    return [f for f in out if f]


def app_font(pixel_size: int):
    """(QFont, сведения для диагностики). Нужен созданный QApplication."""
    from PyQt5.QtGui import QFont, QFontDatabase
    available = QFontDatabase().families()
    system = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    chain = families(available, system.family())
    font = QFont(system)
    if chain:
        font.setFamily(chain[0])
        if hasattr(font, "setFamilies"):                   # Qt ≥ 5.13: явный запасной шрифт для иероглифов
            font.setFamilies(chain)
    font.setPixelSize(pixel_size)
    return font, {"ui": chain[0] if chain else "", "cjk": pick(available, CJK_FONTS), "system": system.family()}
