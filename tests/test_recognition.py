# -*- coding: utf-8 -*-
"""Менеджер провайдеров распознавания (шаги 0.8, 7.1a): выбор, цепочка при неудаче, сторонние провайдеры из
plugins/ocr_*, объединение результатов и учёт времени, правило 8/0, качество."""
import importlib.util
import io
import os
import sys

import pytest

from chipfinder.core.models import ImageVariant, OcrLine, OcrResult
from chipfinder.core.pipeline import ChipPipeline
from chipfinder.core.utils import imread
from chipfinder.recognition import manager as manager_module
from chipfinder.recognition.api import OcrProvider
from chipfinder.recognition.manager import ProviderInfo, RecognitionManager

HAS_PPOCR = all(importlib.util.find_spec(m) for m in ("onnxruntime", "rapidocr_onnxruntime"))
needs_ppocr = pytest.mark.skipif(not HAS_PPOCR, reason="не установлен rapidocr-onnxruntime")
SAMPLES = {"at24c02.png": "24C02", "stm32.png": "STM32F103", "w25q64_rot.png": "W25Q64", "lm358_180.png": "LM358"}
MIN_MYTEST_PCT = 36.0   # таблица шага 0.7: ppocr_main auto 38.3 % на 120 вырезках


def _provider(pid, text="", available=True, fail=False, original=False, cloud=False, lines=None):
    class Provider(OcrProvider):
        id = pid
        title = pid.upper()
        kind = "cloud" if cloud else "local"
        wants_original = original
        calls = []

        def is_available(self):
            return available, "" if available else "нет библиотеки"

        def recognize(self, variants, hints=None, progress=None):
            Provider.calls.append([v.name for v in variants])
            if fail:
                raise RuntimeError("упал")
            texts = lines if lines is not None else ([text] if text else [])
            return OcrResult(lines=[OcrLine(t, 90.0, variants[0].name) for t in texts], best_text=text)

    return Provider


def _manager(ctx, monkeypatch, chain=None, **providers):
    """Провайдеры-примеры подставляются как сторонние (будто найдены в `plugins/ocr_<id>/`)."""
    real_load = manager_module.load_provider_class
    monkeypatch.setattr(manager_module, "discover_plugins",
                        lambda folder: [ProviderInfo(id=pid, title=pid.upper()) for pid in providers])
    monkeypatch.setattr(manager_module, "load_provider_class",
                        lambda info: providers[info.id] if info.id in providers else real_load(info))
    monkeypatch.setitem(ctx.config, "recognition", {"chain": chain or list(providers), "providers": {}})
    return RecognitionManager({}, ctx)


def _write_plugin(plugins, folder, manifest, code):
    path = os.path.join(str(plugins), folder)
    os.makedirs(path)
    with io.open(os.path.join(path, "provider.json"), "w", encoding="utf-8") as f:
        f.write(manifest)
    with io.open(os.path.join(path, "provider.py"), "w", encoding="utf-8") as f:
        f.write(code)


PLUGIN_CODE = u'''# -*- coding: utf-8 -*-
from chipfinder.core.models import OcrLine, OcrResult
from chipfinder.recognition.api import OcrProvider


class Provider(OcrProvider):
    id = "mine"
    title = "Свой способ"

    def recognize(self, variants, hints=None, progress=None):
        text = self.settings.get("answer", "NE555")
        return OcrResult(lines=[OcrLine(text, 88.0, variants[0].name)], best_text=text)
'''


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
    m = _manager(ctx, monkeypatch, chain=["exa", "nosuch"], exa=_provider("exa", available=False))
    assert not m.is_available()
    assert "exa" in m.error and "nosuch" in m.error
    res = m.recognize(VARIANTS)
    assert res.best_text == "" and [a.status for a in res.attempts] == ["unavailable", "unavailable"]


def test_plugin_provider_found_and_chosen(ctx, monkeypatch, tmp_path):
    """Папка `plugins/ocr_<имя>/` (provider.json + provider.py) в пути с русскими буквами и пробелом."""
    app = tmp_path / "Папка программы"
    _write_plugin(app / "plugins", "ocr_mine", u'{"id": "mine", "title": "Свой способ", "kind": "local"}', PLUGIN_CODE)
    _write_plugin(app / "plugins", "ocr_broken", u'{"id": "broken"}', u"raise RuntimeError('сломан')\n")
    _write_plugin(app / "plugins", "ocr_badjson", u"{не json", PLUGIN_CODE)
    _write_plugin(app / "plugins", "ocr_other", u'{"id": "other", "title": "Не в цепочке"}', u"raise SystemExit\n")
    monkeypatch.setattr(ctx, "app_dir", str(app))
    monkeypatch.setitem(ctx.config, "recognition", {"chain": ["broken", "badjson", "mine", "tesseract"],
                                                    "providers": {"mine": {"answer": "LM358"}}})
    m = RecognitionManager({}, ctx)
    by = {i.id: i for i in m.catalog()}
    assert {"mine", "broken", "badjson", "other", "ppocr", "tesseract"} <= set(by)
    assert (by["mine"].title, by["mine"].builtin, by["ppocr"].builtin) == (u"Свой способ", False, True)
    assert by["other"].title == u"Не в цепочке" and not by["other"].error     # код вне цепочки не выполняется
    assert "сломан" in m.problems["broken"] and "provider.json" in m.problems["badjson"]
    res = m.recognize(VARIANTS)
    assert (res.best_text, res.provider, res.provider_title) == ("LM358", "mine", u"Свой способ")
    assert [(a.provider, a.status) for a in res.attempts] == [("broken", "unavailable"), ("badjson", "unavailable"),
                                                              ("mine", "ok")]


def test_plugin_cannot_replace_builtin(ctx, monkeypatch, tmp_path):
    _write_plugin(tmp_path / "plugins", "ocr_fake", u'{"id": "tesseract", "title": "Подмена"}', PLUGIN_CODE)
    monkeypatch.setattr(ctx, "app_dir", str(tmp_path))
    m = RecognitionManager({}, ctx)
    assert m.known["tesseract"].builtin and m.known["tesseract"].title == "Tesseract"


def test_results_merged_and_timed(ctx, monkeypatch):
    """Первый прочитал строки, но итога не дал → итог от второго, строки первого — запасными, без повторов."""
    a = _provider("exa", "", lines=["W25Q64", "lm358"])
    b = _provider("exb", "LM358", lines=["LM358", "2231"])
    c = _provider("exc", "NE555")
    m = _manager(ctx, monkeypatch, exa=a, exf=_provider("exf", fail=True), exb=b, exc=c)
    res = m.recognize(VARIANTS)
    assert (res.best_text, res.provider) == ("LM358", "exb")
    assert [(l.text, l.provider) for l in res.lines] == [("LM358", "exb"), ("2231", "exb"), ("W25Q64", "exa")]
    assert [(x.provider, x.status, x.lines) for x in res.attempts] == [("exa", "empty", 2), ("exf", "failed", 0),
                                                                      ("exb", "ok", 2)]
    assert "упал" in res.attempts[1].detail and not c.calls
    assert res.total_seconds >= res.seconds >= 0
    m.recognize(VARIANTS)
    assert m.stats["exb"]["calls"] == 2 and m.stats["exb"]["ok"] == 2
    assert m.stats["exa"]["empty"] == 2 and m.stats["exf"]["failed"] == 2 and "exc" not in m.stats


def test_cloud_provider_not_called_without_consent(ctx, monkeypatch):
    cloud = _provider("excloud", "NE555", cloud=True)
    m = _manager(ctx, monkeypatch, excloud=cloud, exb=_provider("exb", "LM358"))
    res = m.recognize(VARIANTS, original="фото")
    assert res.provider == "exb" and not cloud.calls
    assert res.attempts[0].status == "unavailable" and "согласия" in res.attempts[0].detail
    m.cloud_consent = lambda provider: True
    assert m.recognize(VARIANTS).provider == "excloud"


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
