# -*- coding: utf-8 -*-
"""Подтверждение документа (шаг 5.1, ARCHITECTURE §4.5)."""
import json
import os

import pytest

from chipfinder.acquire import confirm as C
from chipfinder.acquire.fingerprint import simhash
from chipfinder.acquire.models import AcquisitionRecord, DocFacts, FetchResult, Lead, Verdict
from chipfinder.acquire.verify import SourceTrust

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUST = SourceTrust(makers=("ti.com", "st.com"), catalogs=("alldatasheet.com",))
OWNERS = [["szlcsc.com", "lcsc.com"], ["chipdip.ru", "static.chipdip.ru"]]

BODY = " ".join(
    "The NE555 is a precision timing circuit producing accurate time delays section %d "
    "supply voltage output current threshold level bypass capacitor" % i for i in range(40))
FP = simhash(BODY)
FP_EDITED = simhash(BODY.replace("section 7", "revision B"))
FP_OTHER = simhash(" ".join("Atmel AVR microcontroller flash eeprom sram timer %d serial port" % i for i in range(60)))


def rec(url, sha="aa", fp=FP, status="confirmed", final_url="", reasons=()):
    return AcquisitionRecord(
        part="NE555P", lead=Lead(url=url, kind="pdf"),
        fetch=FetchResult(ok=True, sha256=sha, final_url=final_url or url),
        facts=DocFacts(pages=10, has_text=bool(fp), text_fingerprint=fp),
        verdict=Verdict(status=status, score=80 if status == "confirmed" else 50, reasons=list(reasons)))


@pytest.mark.parametrize("url, site", [
    ("https://www.ti.com/lit/ds/ne555.pdf", "ti.com"),
    ("https://pdf1.alldatasheet.com/a/b.pdf", "alldatasheet.com"),
    ("http://WWW.Padauk.com.tw/x.pdf", "padauk.com.tw"),
    ("https://datasheet.lcsc.com/x.pdf", "lcsc.com"),
    ("https://static.chipdip.ru/lib/1.pdf", "chipdip.ru"),
    ("https://www.example.co.uk/a.pdf", "example.co.uk"),
    ("https://semiconductor.samsung.com/a.pdf", "samsung.com"),
    ("http://192.168.1.10/a.pdf", "192.168.1.10"),
    ("https://localhost/a.pdf", "localhost"),
    ("", ""),
    ("не адрес", ""),
])
def test_site_of(url, site):
    assert C.site_of(url) == site


def test_maker_domain_confirms():
    doc = rec("https://www.ti.com/lit/ds/ne555.pdf")
    got = C.confirm(doc, [], TRUST)
    assert got.confirmed and got.rule == "maker" and got.sites == ["ti.com"]


def test_maker_domain_does_not_confirm_weak_verdict():
    got = C.confirm(rec("https://www.ti.com/ne555.pdf", status="probable"), [], TRUST)
    assert not got.confirmed and got.rule == ""


def test_maker_is_where_file_came_from_not_where_link_was():
    doc = rec("https://www.ti.com/lit/ne555", final_url="https://mirror.example.org/ne555.pdf")
    assert not C.confirm(doc, [], TRUST).confirmed
    doc = rec("https://files.example.org/go?ne555", final_url="https://www.ti.com/lit/ds/ne555.pdf")
    assert C.confirm(doc, [], TRUST).rule == "maker"


def test_two_independent_domains_same_sha():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1", fp="")
    other = rec("https://datasheet4u.com/ne555.pdf", sha="s1", fp="")
    got = C.confirm(doc, [other], TRUST)
    assert got.confirmed and got.rule == "independent"
    assert got.sites == ["alldatasheet.com", "datasheet4u.com"]


def test_two_independent_domains_same_text():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1", fp=FP)
    other = rec("https://datasheet4u.com/ne555.pdf", sha="s2", fp=FP_EDITED)
    assert C.confirm(doc, [other], TRUST).rule == "independent"


def test_same_domain_twice_is_not_confirmation():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1")
    others = [rec("https://pdf1.alldatasheet.com/view/ne555.pdf", sha="s1"),
              rec("http://alldatasheet.com/copy/ne555.pdf", sha="s1")]
    got = C.confirm(doc, others, TRUST)
    assert not got.confirmed and got.sites == ["alldatasheet.com"]


def test_same_owner_is_not_independent():
    doc = rec("https://datasheet.lcsc.com/ne555.pdf", sha="s1")
    other = rec("https://atta.szlcsc.com/ne555.pdf", sha="s1")
    assert not C.confirm(doc, [other], TRUST, owners=OWNERS).confirmed
    assert C.confirm(doc, [other], TRUST).confirmed    # без сведений о владельцах — разные сайты


def test_other_document_or_weak_copy_does_not_confirm():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1")
    assert not C.confirm(doc, [rec("https://datasheet4u.com/avr.pdf", sha="s2", fp=FP_OTHER)], TRUST).confirmed
    assert not C.confirm(doc, [rec("https://datasheet4u.com/ne555.pdf", sha="s1", status="probable")], TRUST).confirmed
    assert not C.confirm(rec("https://a.example.org/1.pdf", sha="s1", status="probable"),
                         [rec("https://datasheet4u.com/ne555.pdf", sha="s1")], TRUST).confirmed


def test_empty_sha_and_fingerprint_never_match():
    doc = rec("https://a.example.org/1.pdf", sha="", fp="")
    other = rec("https://b.example.net/1.pdf", sha="", fp="")
    assert not C.same_document(doc, other)
    assert not C.confirm(doc, [other], TRUST).confirmed


def test_document_itself_in_list_is_ignored():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1")
    assert not C.confirm(doc, [doc], TRUST).confirmed


def test_user_confirms_anything_but_hard_reject():
    got = C.confirm(rec("https://a.example.org/1.pdf", status="needs_user"), [], TRUST, user=True)
    assert got.confirmed and got.rule == "user"
    bad = rec("https://a.example.org/1.pdf", status="rejected", reasons=["hard:active_content"])
    assert not C.confirm(bad, [], TRUST, user=True).confirmed


def test_maker_rule_wins_over_independent_and_lists_all_sites():
    doc = rec("https://www.ti.com/ne555.pdf", sha="s1")
    got = C.confirm(doc, [rec("https://datasheet4u.com/ne555.pdf", sha="s1")], TRUST)
    assert got.rule == "maker" and got.sites == ["ti.com", "datasheet4u.com"]


def test_agreeing_sites_for_e10_ignores_verdicts():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1", status="probable")
    others = [rec("https://datasheet4u.com/ne555.pdf", sha="s1", status="probable"),
              rec("https://pdf.datasheet4u.com/ne555.pdf", sha="s1"),
              rec("https://x.example.org/avr.pdf", sha="s9", fp=FP_OTHER)]
    assert C.agreeing_sites(doc, others) == ["alldatasheet.com", "datasheet4u.com"]
    assert C.agreeing_sites(doc, []) == ["alldatasheet.com"]


def test_apply_keeps_confirmed_and_fills_record():
    doc = rec("https://www.alldatasheet.com/ne555.pdf", sha="s1")
    got = C.confirm(doc, [rec("https://datasheet4u.com/ne555.pdf", sha="s1")], TRUST)
    assert C.apply(doc, got) is doc
    assert doc.verdict.status == "confirmed" and doc.sources_agreeing == ["alldatasheet.com", "datasheet4u.com"]


def test_apply_caps_unconfirmed_at_probable():
    doc = rec("https://files.example.org/ne555.pdf")
    C.apply(doc, C.confirm(doc, [], TRUST))
    assert doc.verdict.status == "probable" and doc.verdict.reasons == ["unconfirmed"]
    assert doc.verdict.score == 80
    weak = rec("https://files.example.org/ne555.pdf", status="needs_user", reasons=["no_text_layer"])
    C.apply(weak, C.confirm(weak, [], TRUST))
    assert weak.verdict.status == "needs_user" and weak.verdict.reasons == ["no_text_layer"]


def test_apply_user_confirmation_raises_status():
    doc = rec("https://files.example.org/ne555.pdf", status="probable", reasons=["no_part_match"])
    C.apply(doc, C.confirm(doc, [], TRUST, user=True))
    assert doc.verdict.status == "confirmed" and "user_confirmed" in doc.verdict.reasons


def test_confirmation_serializes():
    got = C.confirm(rec("https://www.ti.com/ne555.pdf"), [], TRUST)
    assert C.Confirmation.from_json(got.to_json()) == got


def test_owners_from_sources_json():
    with open(os.path.join(ROOT, "data", "sources.json"), encoding="utf-8") as f:
        data = json.load(f)
    owners = C.owners_from_sources(data)
    assert ["szlcsc.com", "lcsc.com"] in owners
    assert all(len(group) > 1 for group in owners)
    # Atmel и Microchip — один сайт: дважды microchip.com не группа
    assert not any(group.count("microchip.com") > 1 for group in owners)
