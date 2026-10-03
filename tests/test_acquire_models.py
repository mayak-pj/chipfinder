# -*- coding: utf-8 -*-
"""Модели получения документов (шаг 1.1): сериализация в dict/JSON туда-обратно."""
import json

import pytest

from chipfinder.acquire.models import (
    AcquisitionRecord, DocFacts, Evidence, FetchResult, Lead, Verdict,
)


def _lead():
    return Lead(url="https://www.st.com/resource/en/datasheet/stm32f103c8.pdf", title="STM32F103x8 数据手册",
                snippet="Микроконтроллер, 64 КБ Flash", source_id="ddg", level="engine", kind="pdf",
                query="STM32F103C8T6 datasheet pdf", language="en", rank_score=0.82)


def _fetch():
    return FetchResult(ok=True, path_in_quarantine="data/quarantine/0123456789abcdef.pdf.quarantine",
                       sha256="ab" * 32, size=1048576, content_type="application/pdf",
                       final_url="https://www.st.com/resource/en/datasheet/cd00161566.pdf")


def _facts():
    return DocFacts(pages=117, has_text=True, title="STM32F103x8 STM32F103xB", producer="Acrobat Distiller",
                    parts_found={"STM32F103C8": [1, 2, 105], "STM32F103C8T6": [105]},
                    family_patterns=["STM32F103x8"], packages=["LQFP48"], manufacturers=["STMicroelectronics"],
                    ordering_codes=["STM32F103C8T6", "STM32F103C8T6TR"], marking_codes=[], language="en",
                    doc_type="datasheet", text_fingerprint="9f3a6c01d2e4b587")


def _verdict():
    return Verdict(status="confirmed", score=85, reasons=["точный партномер на стр. 1"], evidence=[
        Evidence(code="E1", points=30, detail="STM32F103C8 — Medium-density performance line", page=1),
        Evidence(code="E5", points=15, detail="datasheet"),
    ])


def _record():
    return AcquisitionRecord(part="STM32F103C8T6", lead=_lead(), fetch=_fetch(), facts=_facts(),
                             verdict=_verdict(), sources_agreeing=["st.com", "alldatasheet.com"],
                             stored_path="\\\\server\\share\\Библиотека\\confirmed\\ST\\STM32F103C8T6__ddg__abababab.pdf",
                             started_at="2026-10-03T12:00:00Z", finished_at="2026-10-03T12:00:07Z")


@pytest.mark.parametrize("make", [_lead, _fetch, _facts, _verdict, _record])
def test_roundtrip_dict_and_json(make):
    obj = make()
    cls = type(obj)
    assert cls.from_dict(obj.to_dict()) == obj
    assert cls.from_json(obj.to_json()) == obj
    json.dumps(obj.to_dict())  # только простые типы


def test_nested_objects_are_restored_as_models():
    rec = AcquisitionRecord.from_json(_record().to_json())
    assert isinstance(rec.lead, Lead) and isinstance(rec.fetch, FetchResult)
    assert isinstance(rec.facts, DocFacts) and isinstance(rec.verdict, Verdict)
    assert [type(e) for e in rec.verdict.evidence] == [Evidence, Evidence]
    assert rec.facts.parts_found["STM32F103C8"] == [1, 2, 105]


def test_empty_record_roundtrip():
    rec = AcquisitionRecord(part="NE555")
    assert rec.lead is None and rec.verdict is None and rec.sources_agreeing == []
    assert AcquisitionRecord.from_json(rec.to_json()) == rec


def test_json_keeps_non_ascii_readable():
    text = _record().to_json()
    assert "数据手册" in text and "Библиотека" in text and "\\u" not in text


def test_to_dict_is_a_copy():
    facts = _facts()
    d = facts.to_dict()
    d["parts_found"]["STM32F103C8"].append(999)
    d["packages"].append("QFN")
    assert facts.parts_found["STM32F103C8"] == [1, 2, 105] and facts.packages == ["LQFP48"]


def test_from_dict_ignores_unknown_and_fills_defaults():
    lead = Lead.from_dict({"url": "https://example.com/a.pdf", "added_in_future_version": 1})
    assert lead.kind == "page" and lead.rank_score == 0.0 and lead.title == ""
    verdict = Verdict.from_dict({"status": "probable", "evidence": [{"code": "E3", "extra": True}]})
    assert verdict.score == 0 and verdict.evidence == [Evidence(code="E3")]


def test_from_dict_requires_main_field():
    with pytest.raises(ValueError):
        Lead.from_dict({"title": "без адреса"})
    with pytest.raises(ValueError):
        AcquisitionRecord.from_dict({})


def test_parts_found_pages_normalized_after_json():
    facts = DocFacts.from_dict({"parts_found": {"24C02": ["3", 1, 3]}})
    assert facts.parts_found == {"24C02": [1, 3]}


def test_evidence_detail_is_cut_to_150_chars():
    ev = Evidence(code="E1", points=20, detail="я" * 400, page=7)
    assert len(ev.detail) == 150 and ev.detail.endswith("…")
    assert Evidence.from_dict(ev.to_dict()) == ev


def test_invalid_values_rejected():
    with pytest.raises(ValueError):
        Lead(url="https://example.com", kind="video")
    with pytest.raises(ValueError):
        Verdict(status="maybe")


def test_verdict_score_clamped_and_helpers():
    assert Verdict(status="confirmed", score=140).score == 100
    assert Verdict(status="rejected", score=-25).score == 0
    v = _verdict()
    assert v.has("E1") and not v.has("E11")
