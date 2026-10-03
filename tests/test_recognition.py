# -*- coding: utf-8 -*-
"""Менеджер провайдеров распознавания (шаг 0.8): выбор, цепочка при неудаче, правило 8/0, качество."""
import importlib.util
import os
import sys
import types

import pytest

from chipfinder.core.models import ImageVariant, OcrLine, OcrResult
from chipfinder.core.pipeline import ChipPipeline
from chipfinder.core.utils import imread
from chipfinder.recognition.api import OcrProvider
from chipfinder.recognition.manager import RecognitionManager

HAS_PPOCR = all(importlib.util.find_spec(m) for m in ("onnxruntime", "rapidocr_onnxruntime"))
needs_ppocr = pytest.mark.skipif(not HAS_PPOCR, reason="не установлен rapidocr-onnxruntime")
SAMPLES = {"at24c02.png": "24C02", "stm32.png": "STM32F103", "w25q64_rot.png": "W25Q64", "lm358_180.png": "LM358"}
MIN_MYTEST_PCT = 36.0   # таблица шага 0.7: ppocr_main auto 38.3 % на 120 вырезках


def _provider(pid, text="", available=True, fail=False, original=False):
    class Provider(OcrProvider):
        id = pid
        title = pid.upper()
        wants_original = original
        calls = []

        def is_available(self):
            return available, "" if available else "нет библиотеки"

        def recognize(self, variants, hints=None, progress=None):
            Provider.calls.append([v.name for v in variants])
            if fail:
                raise RuntimeError("упал")
            return OcrResult(lines=[OcrLine(text, 90.0, variants[0].name)] if text else [], best_text=text)

    return Provider


def _manager(ctx, monkeypatch, **providers):
    """Провайдеры-примеры подключаются как плагины `ocr_<id>/provider.py`."""
    for pid, cls in providers.items():
        mod = types.ModuleType("ocr_%s.provider" % pid)
        mod.Provider = cls
        monkeypatch.setitem(sys.modules, "ocr_%s" % pid, types.ModuleType("ocr_%s" % pid))
        monkeypatch.setitem(sys.modules, "ocr_%s.provider" % pid, mod)
    monkeypatch.setitem(ctx.config, "recognition", {"chain": list(providers), "providers": {}})
    return RecognitionManager({}, ctx)


VARIANTS = [ImageVariant("gray", None)]


def test_default_role_is_manager(ctx):
    ocr = ctx.modules["ocr"]
    assert isinstance(ocr, RecognitionManager)
    assert ocr.chain == ["ppocr", "tesseract"]


def test_example_provider_is_chosen(ctx, monkeypatch):
    a, b = _provider("exa", "NE555"), _provider("exb", "LM358")
    m = _manager(ctx, monkeypatch, exa=a, exb=b)
    res = m.recognize(VARIANTS)
    assert (res.best_text, res.provider, res.provider_title) == ("NE555", "exa", "EXA")
    assert not b.calls


def test_wants_original_gets_photo(ctx, monkeypatch):
    a = _provider("exa", "NE555", original=True)
    _manager(ctx, monkeypatch, exa=a).recognize(VARIANTS, original="фото")
    assert a.calls == [["original"]]


@pytest.mark.parametrize("first", [dict(available=False), dict(fail=True), dict(text="")])
def test_next_provider_when_first_fails(ctx, monkeypatch, first):
    m = _manager(ctx, monkeypatch, exa=_provider("exa", **first), exb=_provider("exb", "LM358"))
    assert m.is_available()
    res = m.recognize(VARIANTS)
    assert (res.best_text, res.provider) == ("LM358", "exb")


def test_nothing_available(ctx, monkeypatch):
    m = _manager(ctx, monkeypatch, exa=_provider("exa", available=False), nosuch=_provider("x"))
    monkeypatch.delitem(sys.modules, "ocr_nosuch.provider")
    m = RecognitionManager({}, ctx)
    assert not m.is_available()
    assert "exa" in m.error and "nosuch" in m.error
    assert m.recognize(VARIANTS).best_text == ""


def test_missing_ppocr_falls_back_to_tesseract(ctx, monkeypatch):
    """Нет библиотек PP-OCR (или не загрузились DLL) → читает Tesseract."""
    m = ctx.modules["ocr"]
    monkeypatch.setattr(m.providers["ppocr"], "_error", "PP-OCR не загрузился: ImportError: DLL load failed")
    monkeypatch.setattr(m.providers["tesseract"], "is_available", lambda: (True, ""))
    monkeypatch.setattr(m.providers["tesseract"], "recognize", lambda v, hints=None, progress=None: OcrResult(best_text="LM358"))
    res = m.recognize(VARIANTS, original=None)
    assert res.provider == "tesseract" and "DLL" in m.error


def test_slashed_zero_rule(ctx):
    """Перечёркнутый ноль прочитан как 8: «STM32F183C8T6» → в кандидатах есть STM32F103C8T6."""
    ident = ctx.modules["identifier"]
    cands = ident.identify("W25Q64JV\nSTM32F183C8T6")
    assert "STM32F103C8T6" in [c.part for c in cands]
    cands = ident.identify("PIC16F628A")
    assert cands[0].part == "PIC16F628A"      # настоящая восьмёрка не портится


@needs_ppocr
@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_ppocr_reads_samples(ctx, samples_dir, name):
    """Образцы читаются PP-OCR не хуже v1 (Tesseract): партномер определён."""
    ctx.config["recognition"]["chain"] = ["ppocr"]
    ctx.modules["ocr"] = RecognitionManager({}, ctx)
    r, _v = ChipPipeline(ctx).analyze_image(os.path.join(samples_dir, name))
    assert r.ocr.provider == "ppocr" and r.ocr.seconds > 0
    assert SAMPLES[name] in r.chosen_part


@pytest.mark.needs_tesseract
@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_tesseract_chain_reads_samples(ctx, samples_dir, name):
    ctx.config["recognition"]["chain"] = ["tesseract"]
    ctx.modules["ocr"] = RecognitionManager({}, ctx)
    r, _v = ChipPipeline(ctx).analyze_image(os.path.join(samples_dir, name))
    assert r.ocr.provider == "tesseract"
    assert SAMPLES[name] in r.chosen_part


@needs_ppocr
@pytest.mark.mytest
def test_mytest_ocr_share(ctx, my_test_dir):
    """Доля вырезок my_test/ocr, где строка из имени файла прочитана (критерий шага 0.7)."""
    folder = os.path.join(my_test_dir, "ocr")
    if not os.path.isdir(folder):
        pytest.skip("нет папки my_test/ocr/")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "win7_pack"))
    import ppocr_check as pc
    items = pc.collect(folder)
    if not items:
        pytest.skip("в my_test/ocr/ нет картинок")
    ocr = ctx.modules["ocr"]
    exact = inside = 0
    for path, expected in items:
        res = ocr.recognize([], original=imread(path))
        exact += any(pc.is_match(l.text, expected) for l in res.lines)
        inside += any(expected and expected in pc.norm(l.text) for l in res.lines)
    pct = 100.0 * exact / len(items)
    print("\nmy_test/ocr: %d фото, строка совпала — %.1f %%, строка содержит ответ — %.1f %%"
          % (len(items), pct, 100.0 * inside / len(items)))
    assert pct >= MIN_MYTEST_PCT
