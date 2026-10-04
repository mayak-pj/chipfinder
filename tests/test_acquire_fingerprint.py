# -*- coding: utf-8 -*-
"""Отпечаток текста и поиск дублей (шаг 4.4)."""

from chipfinder.acquire.extract import DocText, extract_facts, facts_from_text
from chipfinder.acquire.fingerprint import (
    find_similar, group_duplicates, is_duplicate, similarity, simhash)
from tests.fixtures import make_pdfs

BODY = " ".join(
    "The NE555 is a precision timing circuit producing accurate time delays section %d "
    "supply voltage output current threshold level bypass capacitor" % i for i in range(40))


def facts_of(*pages, **kw):
    doc = DocText(pages=len(pages), texts={i + 1: t for i, t in enumerate(pages)}, **kw)
    return facts_from_text(doc)


def test_format_and_determinism():
    fp = simhash(BODY)
    assert len(fp) == 16 and int(fp, 16) >= 0
    assert simhash(BODY) == fp


def test_same_text_other_case_spacing_is_duplicate():
    other = "  ".join(BODY.upper().split(" "))
    assert similarity(simhash(BODY), simhash(other)) == 1.0


def test_small_edit_is_duplicate_other_text_is_not():
    edited = BODY.replace("section 7", "revision B").replace("section 21", "Rev. 2019")
    assert is_duplicate(simhash(BODY), simhash(edited))
    different = " ".join("Atmel AVR microcontroller flash eeprom sram timer %d serial port" % i for i in range(60))
    assert not is_duplicate(simhash(BODY), simhash(different))


def test_short_or_empty_text_has_no_fingerprint():
    assert simhash("") == "" and simhash("NE555 timer") == ""
    assert similarity("", simhash(BODY)) == 0.0
    assert similarity("", "") == 0.0
    assert similarity("zz", simhash(BODY)) == 0.0


def test_chinese_text_by_characters():
    a = "这是一个精密定时器芯片的数据手册，电源电压范围从四点五伏到十六伏，输出电流可达两百毫安。" * 3
    assert simhash(a) != "" and is_duplicate(simhash(a), simhash(a + "第二版"))


def test_find_similar_and_groups():
    known = {"a": simhash(BODY), "b": simhash("Atmel AVR flash eeprom sram timer serial port " * 20), "c": ""}
    assert find_similar(simhash(BODY.upper()), known) == ("a", 1.0)
    assert find_similar(simhash("completely different words " * 30), known) is None
    items = dict(known, d=simhash(BODY + " extra"), e=simhash("Atmel AVR flash eeprom sram timer serial port " * 20))
    assert group_duplicates(items) == [["a", "d"], ["b", "e"]]


def test_facts_fingerprint_ignores_metadata():
    one = facts_of(BODY, title="NE555 datasheet", producer="Acme")
    two = facts_of(BODY, title="Другое название", producer="Other PDF")
    assert one.text_fingerprint and one.text_fingerprint == two.text_fingerprint


def test_scan_has_no_fingerprint():
    assert facts_of("").text_fingerprint == ""


def test_pdf_same_content_other_metadata(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    pages = [["NE555 Precision Timer"] + [BODY.split(" section ")[i % 40] + " line %d" % i for i in range(25)]]
    a.write_bytes(make_pdfs.make_pdf(pages, title="One", producer="P1"))
    b.write_bytes(make_pdfs.make_pdf(pages, title="Two", producer="P2", author="X"))
    fa, fb = extract_facts(str(a)), extract_facts(str(b))
    assert fa.text_fingerprint and fa.text_fingerprint == fb.text_fingerprint


def test_pdf_different_documents_differ(tmp_path):
    ds = tmp_path / "d.pdf"
    ch = tmp_path / "c.pdf"
    ds.write_bytes(make_pdfs.DOCS["datasheet"]())
    ch.write_bytes(make_pdfs.DOCS["chinese"]())
    assert not is_duplicate(extract_facts(str(ds)).text_fingerprint, extract_facts(str(ch)).text_fingerprint)
