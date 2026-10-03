# -*- coding: utf-8 -*-
"""Менеджер провайдеров распознавания — реализация роли `ocr`.

Цепочка из `config → recognition.chain`: следующий провайдер пробуется, если текущий недоступен,
упал или ничего не прочитал. О конкретных провайдерах менеджер не знает — ищет их по id.
"""
from __future__ import annotations

import importlib
import time
from typing import Dict, List, Optional, Tuple

from ..core.interfaces import OCR, ProgressFn
from ..core.models import ImageVariant, OcrResult
from .api import OcrProvider

DEFAULT_CHAIN = ["ppocr", "tesseract"]


def load_provider_class(pid: str):
    """Встроенный `recognition/providers/<id>` или сторонний `plugins/ocr_<id>/provider.py`."""
    errors = []
    for mod_name in ("chipfinder.recognition.providers.%s" % pid, "ocr_%s.provider" % pid):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as e:
            errors.append(str(e))
            continue
        cls = getattr(mod, "Provider", None)
        if cls is None or not issubclass(cls, OcrProvider):
            raise ImportError("в %s нет класса Provider(OcrProvider)" % mod_name)
        return cls
    raise ImportError("; ".join(errors))


class RecognitionManager(OCR):
    name = "recognition"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        cfg = ctx.config.get("recognition", {})
        self.chain: List[str] = list(cfg.get("chain") or DEFAULT_CHAIN)
        self.providers: Dict[str, OcrProvider] = {}
        self.problems: Dict[str, str] = {}      # id → почему не работает
        for pid in self.chain:
            try:
                self.providers[pid] = load_provider_class(pid)(cfg.get("providers", {}).get(pid, {}), ctx)
            except Exception as e:  # noqa — сломанный провайдер не должен ронять программу
                self.problems[pid] = "не загрузился: %s" % e
                ctx.log.warning("Провайдер распознавания %s не загрузился: %s", pid, e)

    def _available(self, pid: str) -> Tuple[bool, str]:
        p = self.providers.get(pid)
        if p is None:
            return False, self.problems.get(pid, "нет такого провайдера")
        try:
            ok, why = p.is_available()
        except Exception as e:  # noqa
            ok, why = False, str(e)
        if not ok:
            self.problems[pid] = why
        return ok, why

    def is_available(self) -> bool:
        return any(self._available(pid)[0] for pid in self.chain)

    @property
    def error(self) -> str:
        return "; ".join("%s — %s" % (pid, self.problems[pid]) for pid in self.chain if pid in self.problems)

    def recognize(self, variants: List[ImageVariant], progress: Optional[ProgressFn] = None,
                  original=None) -> OcrResult:
        say = progress or (lambda m: None)
        last = OcrResult()
        for pid in self.chain:
            ok, why = self._available(pid)
            if not ok:
                self.ctx.log.info("Распознавание: %s недоступен (%s)", pid, why)
                continue
            p = self.providers[pid]
            imgs = variants
            if p.wants_original and original is not None:
                imgs = [ImageVariant(name="original", image=original)]
            say("Распознаю: %s" % p.title)
            t0 = time.time()
            try:
                res = p.recognize(imgs, hints={}, progress=progress)
            except Exception as e:  # noqa — упал → следующий в цепочке
                self.problems[pid] = "ошибка: %s" % e
                self.ctx.log.exception("Провайдер распознавания %s упал", pid)
                continue
            res.provider = pid
            res.provider_title = p.title
            res.seconds = round(time.time() - t0, 2)
            self.ctx.log.info("Распознавание: %s, %.2f с, строк %d", pid, res.seconds, len(res.lines))
            last = res
            if res.best_text.strip():
                return res
        return last

    def close(self) -> None:
        for p in self.providers.values():
            try:
                p.close()
            except Exception:  # noqa
                pass
