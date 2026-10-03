# -*- coding: utf-8 -*-
"""План запросов на трёх языках (шаг 1.3, ARCHITECTURE §4.7)."""
import pytest

from chipfinder.acquire.query import base_part, family, is_smd_code, plan_queries


@pytest.mark.parametrize("raw, base", [
    ("STM32F103C8T6", "STM32F103C8"),
    ("AT24C02N-10SU-2.7", "AT24C02"),
    ("W25Q64JVSIQ", "W25Q64JV"),
    ("LM358DR", "LM358"),
    ("PMS150C-U06", "PMS150C"),
    ("lm358 dr", "LM358"),
    ("LM358/TR", "LM358"),
    ("NE555P", "NE555"),
    ("A6W", "A6W"),
    ("", ""),
])
def test_base_part(raw, base):
    assert base_part(raw) == base


@pytest.mark.parametrize("raw, fam", [
    ("STM32F103C8T6", "STM32F103"), ("W25Q64JVSIQ", "W25Q64"), ("PMS150C-U06", "PMS150"),
    ("LM358DR", ""), ("A6W", ""),
])
def test_family(raw, fam):
    assert family(raw) == fam


def test_is_smd_code():
    assert is_smd_code("A6W") and is_smd_code("1AM")
    assert not is_smd_code("LM358") and not is_smd_code("ABCD") and not is_smd_code("")


def _texts(qs, lang):
    return [q.text for q in qs if q.lang == lang]


def test_plan_stm32():
    qs = plan_queries("STM32F103C8T6")
    assert [q.lang for q in qs][0] == "en"
    assert _texts(qs, "en") == ["STM32F103C8 datasheet pdf", "STM32F103 datasheet pdf"]
    assert "STM32F103C8 数据手册" in _texts(qs, "zh")
    assert "STM32F103C8 даташит" in _texts(qs, "ru")


def test_plan_no_family_for_lm358():
    qs = plan_queries("LM358DR")
    assert all(q.kind == "part" for q in qs)
    assert _texts(qs, "en") == ["LM358 datasheet pdf"]
    assert _texts(qs, "zh") == ["LM358 数据手册", "LM358 规格书 pdf", "LM358 中文资料"]
    assert _texts(qs, "ru") == ["LM358 даташит", "LM358 документация pdf"]


def test_plan_smd_code():
    qs = plan_queries("A6W")
    assert {q.kind for q in qs} == {"smd"}
    assert _texts(qs, "zh") == ["A6W 丝印"]
    assert _texts(qs, "ru") == ["A6W маркировка smd"]


def test_language_order_from_config():
    qs = plan_queries("AT24C02N-10SU-2.7", langs=["ru", "en"])
    langs = []
    for q in qs:
        if not langs or langs[-1] != q.lang:
            langs.append(q.lang)
    assert langs == ["ru", "en"]


def test_unknown_langs_fall_back_to_default():
    assert {q.lang for q in plan_queries("LM358", langs=["xx"])} == {"en", "zh", "ru"}
