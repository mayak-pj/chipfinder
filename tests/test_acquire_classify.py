# -*- coding: utf-8 -*-
"""Тип документа (шаг 4.2). PDF — синтетические, из tests/fixtures/make_pdfs.py."""
import pytest

from digger.acquire import classify as C
from digger.acquire.extract import DocText, facts_from_text
from digger.acquire.models import DocFacts
from tests.fixtures import make_pdfs


def kind(*pages, **kw):
    doc = DocText(pages=kw.pop("total", len(pages)), texts={i + 1: t for i, t in enumerate(pages)},
                  heading=kw.pop("heading", ""), title=kw.pop("title", ""))
    return C.classify(doc, facts_from_text(doc))


@pytest.mark.parametrize("name, expected", [
    ("datasheet", C.DATASHEET),
    ("family", C.FAMILY),
    ("app_note", C.APP_NOTE),
    ("catalog", C.CATALOG),
    ("foreign", C.DATASHEET),            # чужой партномер — дело улик (4.3), тип у документа правильный
    ("scan", C.UNKNOWN),
    ("chinese", C.DATASHEET),
    ("errata", C.ERRATA),
    ("reference_manual", C.REFERENCE_MANUAL),
    ("product_brief", C.PRODUCT_BRIEF),
    ("distributor", C.DISTRIBUTOR),
    ("unrelated", C.UNRELATED),
])
def test_fixture_types(tmp_path, name, expected):
    facts = C.classify_file(make_pdfs.write(name, tmp_path))
    assert isinstance(facts, DocFacts)
    assert facts.doc_type == expected
    assert facts.doc_type in C.TYPES


def test_all_fixtures_covered():
    assert set(make_pdfs.DOCS) == {"datasheet", "family", "app_note", "catalog", "foreign", "scan", "chinese",
                                   "errata", "reference_manual", "product_brief", "distributor", "unrelated"}


def test_unreadable_file_is_unknown(tmp_path):
    path = tmp_path / "битый.pdf"
    path.write_bytes(b"%PDF-1.4 not really")
    assert C.classify_file(str(path)).doc_type == C.UNKNOWN


def test_datasheet_without_the_word():
    """Слова «datasheet» нет — тип узнаётся по разделам."""
    assert kind("LM358\nFeatures\nWide supply range\nAbsolute Maximum Ratings\nElectrical Characteristics\n"
                "Pin Configuration", heading="LM358") == C.DATASHEET


def test_sections_deep_in_document_with_broken_spaces():
    """Как в настоящем PDF: обложка без слов, разделы далеко, pypdf разорвал слова пробелами."""
    pages = ["W25Q512JV\n3V 512M-BIT\nSERIAL FLASH MEMORY"] + ["W25Q512JV\nstatus register bits"] * 8
    pages[3] = "W25Q512JV\n3. PACKAGE TYPES AND PI N CONFIGURATIONS"
    pages[7] = "W25Q512JV\n9.1 Absolute Maximum  Rat ings\n9.3 DC Electrical Characteristics"
    assert kind(*pages, heading="W25Q512JV") == C.DATASHEET


def test_datasheet_mentions_app_note_and_errata():
    """Ссылки на другие документы в тексте не меняют тип."""
    text = ("W25Q64JV 3V 64M-bit serial flash memory\nDatasheet\nFeatures\nAbsolute Maximum Ratings\n"
            "See application note AN4760 and the errata sheet for details.\nOrdering Information\nW25Q64JVSSIQ")
    assert kind(text, heading="W25Q64JV") == C.DATASHEET


def test_app_note_mentions_datasheet_sections():
    text = ("AN2606 Application note\nSTM32 system memory boot mode\nIntroduction\n"
            "For the electrical characteristics and absolute maximum ratings refer to the datasheet.")
    assert kind(text, heading="AN2606 Application note") == C.APP_NOTE


def test_heading_beats_metadata_title():
    """Заголовок в метаданных остался от другого документа — верим первой странице."""
    text = "ATmega328P\nErrata\nRev. D\n1. Wrong values read after erase\nProblem fix / workaround"
    assert kind(text, heading="ATmega328P Errata", title="ATmega328P datasheet") == C.ERRATA


def test_russian_and_chinese_words():
    assert kind("К155ЛА3\nТехническое описание\nПредельно допустимые режимы\nЭлектрические параметры\n"
                "Назначение выводов", heading="К155ЛА3") == C.DATASHEET
    assert kind("Микросхемы серии 1533\nКаталог продукции 2020\nК1533ЛА3 К1533ЛЕ1",
                heading="Каталог продукции 2020") == C.CATALOG
    assert kind("STM32F103 应用笔记\n本应用笔记介绍 ADC 的使用方法", heading="STM32F103 应用笔记") == C.APP_NOTE
    assert kind("CH340G 规格书\n特点\n极限参数\n电气特性\n引脚说明", heading="CH340G") == C.DATASHEET
    assert kind("产品选型手册 2024\nGD25Q64C GD25Q32C GD25Q16C", heading="产品选型手册 2024") == C.CATALOG


def test_family_by_enumeration_in_heading():
    text = "AT24C01/02/04\nTwo-wire Serial EEPROM\nFeatures\nAbsolute Maximum Ratings\nPin Configurations"
    assert kind(text, heading="AT24C01/02/04") == C.FAMILY


def test_family_pattern_only_in_body_is_plain_datasheet():
    text = ("STM32F103C8T6 Datasheet\nFeatures\nElectrical Characteristics\nAbsolute Maximum Ratings\n"
            "This device belongs to the STM32F103xx family.")
    assert kind(text, heading="STM32F103C8T6 Datasheet") == C.DATASHEET


def test_many_parts_without_sections_is_catalog():
    names = ["%s%d%s" % (p, 100 + 3 * i, s) for i in range(12) for p, s in (("LM", "N"), ("TL", "CD"), ("MC", "P"))]
    assert kind("Linear products\n" + "\n".join(names[:18]), "\n".join(names[18:])) == C.CATALOG


def test_parts_without_any_signs_is_unknown_and_no_parts_is_unrelated():
    assert kind("NE555 and LM358 are mentioned in this text about something else entirely.") == C.UNKNOWN
    assert kind("Minutes of the meeting held on Monday, nothing about components here.") == C.UNRELATED


def test_distributor_needs_several_signs():
    """Одно слово «price» в datasheet не делает его страницей магазина."""
    text = ("NE555 Datasheet\nFeatures\nLow price and high stability\nAbsolute Maximum Ratings\n"
            "Electrical Characteristics")
    assert kind(text, heading="NE555 Datasheet") == C.DATASHEET
    shop = "LCSC Electronics\nNE555DR\nIn Stock: 5000\nUnit Price\nAdd to Cart\nMinimum order quantity 1"
    assert kind(shop) == C.DISTRIBUTOR
    assert kind(shop, total=40) != C.DISTRIBUTOR          # многостраничный документ — не страница магазина


def test_scan_with_telling_title():
    doc = DocText(pages=3, texts={1: "", 2: ""}, title="AT24C02 datasheet")
    assert C.classify(doc, facts_from_text(doc)) == C.DATASHEET


def test_scores_explain_choice():
    doc = DocText(pages=1, texts={1: "AN1234 Application note\nNE555 dimmer"}, heading="AN1234 Application note")
    s = C.scores(doc, facts_from_text(doc))
    assert s[C.APP_NOTE] > s[C.DATASHEET] and set(s) <= set(C.TYPES)
