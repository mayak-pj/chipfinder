# -*- coding: utf-8 -*-
"""Тип документа (ARCHITECTURE §4.2; шаг 4.2).

`classify` — чистая функция: по прочитанному тексту (`DocText`) и фактам (`DocFacts`) называет тип:
datasheet, datasheet семейства, app note, errata, reference manual, каталог, страница дистрибьютора,
product brief, «не относится», «не определён». `classify_file` — чтение, факты и тип одним вызовом.

Тип — это «что за документ», а не «о том ли чипе»: datasheet на чужой партномер остаётся datasheet,
подходит ли он — решают улики (`verify.py`, E5 берёт тип отсюда).

Слова типа весят по месту: заголовок первой страницы > заголовок в метаданных > первые строки > текст
первых страниц. Поэтому ссылка «см. application note» в datasheet и «см. datasheet» в app note тип не меняют.
Datasheet узнаётся ещё и по разделам (Features, Absolute Maximum Ratings…) — слова «datasheet» в нём может не
быть, а разделы разбросаны по всему документу. pypdf рвёт слова пробелами («PI N CONFIGURATIONS»), поэтому
разделы ищутся в тексте без пробелов.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

from .extract import DocText, facts_from_text, read_document
from .models import DocFacts

DATASHEET = "datasheet"
FAMILY = "family"                         # datasheet на семейство: в заголовке шаблон (STM32F103x8, 24C01/02/04)
APP_NOTE = "app_note"                     # сюда же руководства к отладочным платам
ERRATA = "errata"
REFERENCE_MANUAL = "reference_manual"
CATALOG = "catalog"
DISTRIBUTOR = "distributor_page"
PRODUCT_BRIEF = "product_brief"
UNRELATED = "unrelated"                   # текст есть, но это не документ о микросхемах
UNKNOWN = "unknown"                       # скан без текста, нечитаемый файл или признаков мало
TYPES = (DATASHEET, FAMILY, APP_NOTE, ERRATA, REFERENCE_MANUAL, CATALOG, DISTRIBUTOR, PRODUCT_BRIEF,
         UNRELATED, UNKNOWN)

W_HEADING, W_TITLE, W_TOP, W_BODY = 4, 3, 2, 1
TOP_LINES = 12                # «первые строки» первой страницы
BODY_PAGES = 2                # слова типа ищутся в начале документа
SECTIONS_MAX = 3              # больше разделы к баллу не добавляют: заголовок другого типа должен перевесить
MIN_SCORE = 2                 # одно упоминание в тексте типом не считается
CATALOG_PARTS = 25            # столько разных партномеров без разделов datasheet — перечень, а не описание
SHOP_PAGES = 5                # страница магазина, сохранённая в PDF, длинной не бывает
SHOP_SIGNS = 3                # признаков торговли без названия магазина; с названием хватит двух
SHOP_SCORE = W_HEADING + W_TITLE + W_TOP + W_BODY

_I = re.I
_WORDS: List[Tuple[str, Any]] = [          # порядок — старшинство при равных баллах
    (ERRATA, re.compile(r"\berrat(?:a|um)\b|device limitations|勘误|список ошибок|перечень ошибок", _I)),
    (APP_NOTE, re.compile(r"application\s+notes?|\bapp\.?\s?note|(?-i:(?<![A-Za-z0-9])AN[- ]?\d{3,5}(?![A-Za-z0-9]))|"
                          r"user'?s?\s+guide|evaluation\s+(?:board|kit)|design\s+guide|"
                          r"应用笔记|应用指南|应用手册|使用指南|"
                          r"(?:руководств|рекомендаци)\w*\s+по\s+применению|руководство\s+пользователя", _I)),
    (REFERENCE_MANUAL, re.compile(r"reference\s+manual|programming\s+manual|user'?s?\s+manual|"
                                  r"programmer'?s?\s+(?:guide|manual|reference)|参考手册|编程手册|"
                                  r"справочное\s+руководство|руководство\s+программиста", _I)),
    (PRODUCT_BRIEF, re.compile(r"product\s+brief|data\s+brief|fact\s+sheet|\bflyer\b|产品简介", _I)),
    (CATALOG, re.compile(r"select(?:ion|or)\s+guide|product\s+(?:catalog(?:ue)?|guide|selector)|"
                         r"short[- ]form\s+catalog|line\s?card|选型手册|选型指南|选型表|产品目录|"
                         r"каталог|номенклатур", _I)),
    (DATASHEET, re.compile(r"data\s?sheets?|datenblatt|product\s+specification|preliminary\s+specification|"
                           r"数据手册|数据表|规格书|技术手册|产品规格|產品規格|"
                           r"техническое\s+описание|технические\s+условия|спецификаци", _I)),
]
_SECTIONS = [re.compile(p, _I) for p in (          # применяются к тексту без пробелов
    r"features|特性|特点|особенности",
    r"absolutemaximumratings|极限参数|绝对最大额定|предельн\w+?(?:допустим|режим|значени)",
    r"(?:electrical|[ad]c)characteristics|电气特性|电气参数|电特性|электрические(?:параметры|характеристики)",
    r"(?:pin|pad|ball)(?:configurations?|descriptions?|assignments?)|引脚|管脚|назначениевыводов|цоколевк",
    r"order(?:ing)?(?:information|codes?)|订购信息|订货信息|информациядлязаказа",
    r"recommendedoperatingconditions|推荐工作条件|рекомендуемые(?:условия|режимы)",
    r"package(?:outlines?|dimensions|information|specifications?)|mechanicaldata|封装尺寸|封装信息|габаритн",
    r"blockdiagram|功能框图|структурнаясхема",
)]
_SHOP_SIGNS = [re.compile(p, _I) for p in (
    r"\bin\s+stock\b|库存|в\s+наличии", r"unit\s+price|单价|цена\s+за", r"add\s+to\s+(?:cart|basket)|加入购物车|в\s+корзину",
    r"price\s+breaks?|价格梯度|оптовые\s+цены", r"\bbuy\s+now\b|立即购买|купить", r"minimum\s+order|起订量|минимальный\s+заказ",
    r"lead\s+time|货期|срок\s+поставки",
)]
_SHOP_NAMES = re.compile(r"digi-?key|mouser|\blcsc\b|farnell|element14|newark|arrow\s+electronics|rs\s+components|"
                         r"\btme\b|立创商城|云汉芯城|chipdip|чип\s+и\s+дип|компэл|платан", _I)


def classify_file(path: str, hints: Sequence[str] = ()) -> DocFacts:
    """Факты документа с заполненным `doc_type`. Нечитаемый файл — `unknown`; исключений не бросает."""
    doc = read_document(path)
    facts = facts_from_text(doc, hints)
    facts.doc_type = classify(doc, facts)
    return facts


def scores(doc: DocText, facts: DocFacts) -> Dict[str, int]:
    """Баллы типов — по ним видно, почему выбран тип (журнал, отладка)."""
    numbers = sorted(doc.texts)
    first = doc.texts.get(1, "") if facts.has_text else ""
    top = "\n".join([line for line in first.splitlines() if line.strip()][:TOP_LINES])
    body = "\n".join(doc.texts[n] for n in numbers[:BODY_PAGES]) if facts.has_text else ""
    places = ((facts.heading, W_HEADING), (facts.title, W_TITLE), (top, W_TOP), (body, W_BODY))
    out = {kind: sum(w for text, w in places if text and rx.search(text)) for kind, rx in _WORDS}
    if facts.has_text:
        squeezed = "".join("".join(doc.texts[n].split()) for n in numbers)
        out[DATASHEET] += min(SECTIONS_MAX, sum(1 for rx in _SECTIONS if rx.search(squeezed)))
    out[DISTRIBUTOR] = SHOP_SCORE if _is_shop(doc, facts) else 0
    return out


def classify(doc: DocText, facts: DocFacts) -> str:
    table = scores(doc, facts)
    order = [DISTRIBUTOR] + [kind for kind, _ in _WORDS]
    best = max(order, key=lambda kind: (table[kind], -order.index(kind)))
    if table[best] < MIN_SCORE:
        if not facts.has_text:
            return UNKNOWN
        if len(facts.parts_found) >= CATALOG_PARTS:
            return CATALOG
        return UNKNOWN if facts.parts_found or facts.family_patterns else UNRELATED
    if best == DATASHEET and _family_in_heading(facts):
        return FAMILY
    return best


def _is_shop(doc: DocText, facts: DocFacts) -> bool:
    if not facts.has_text or doc.pages > SHOP_PAGES:
        return False
    text = "\n".join(doc.texts[n] for n in sorted(doc.texts))
    signs = sum(1 for rx in _SHOP_SIGNS if rx.search(text))
    return signs >= SHOP_SIGNS or (signs >= SHOP_SIGNS - 1 and bool(_SHOP_NAMES.search(text + "\n" + facts.title)))


def _family_in_heading(facts: DocFacts) -> bool:
    where = re.sub(r"\s+", "", facts.heading + "\n" + facts.title)
    return any(p in where for p in facts.family_patterns)
