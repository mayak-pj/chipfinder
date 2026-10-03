# -*- coding: utf-8 -*-
"""Провайдер PP-OCRv4 (rapidocr-onnxruntime + onnxruntime, без интернета).

Читает исходное фото без улучшения OpenCV; поворот (0/90/180/270) выбирается по лучшей суммарной
уверенности — так же, как вариант `auto` в бенче `tools/win7_pack/ppocr_check.py`.
"""
from __future__ import annotations

import importlib.util
import re
import threading
from typing import List, Tuple

from ...api import OcrProvider
from ....core.models import OcrLine, OcrResult
from ....modules.enhance_opencv import rotate


def _alnum(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", s))


class Provider(OcrProvider):
    id = "ppocr"
    title = "PP-OCRv4"
    languages = ("en", "zh")
    wants_original = True

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        self._engine = None
        self._error = ""
        self._lock = threading.Lock()

    def is_available(self) -> Tuple[bool, str]:
        if self._error:
            return False, self._error
        for mod in ("onnxruntime", "rapidocr_onnxruntime"):
            if importlib.util.find_spec(mod) is None:
                return False, "не установлен пакет %s" % mod
        return True, ""

    def _load(self):
        """Модели загружаются при первом распознавании (в фоновом потоке, не при запуске окна)."""
        with self._lock:
            if self._engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    self._engine = RapidOCR()
                except BaseException as e:  # noqa — на Win7 это может быть ошибка загрузки DLL
                    self._error = "PP-OCR не загрузился: %s: %s" % (type(e).__name__, e)
                    raise RuntimeError(self._error)
            return self._engine

    def _read(self, img) -> List[Tuple[str, float]]:
        s = self.settings
        res, _t = self._load()(img, text_score=float(s.get("text_score", 0.3)),
                               box_thresh=float(s.get("det_box_thresh", 0.3)),
                               unclip_ratio=float(s.get("det_unclip_ratio", 1.8)))
        return [(str(r[1]), float(r[2])) for r in (res or [])]

    def recognize(self, variants, hints=None, progress=None) -> OcrResult:
        result = OcrResult()
        if not variants:
            return result
        v = variants[0]
        best_rot, best_lines, best_w = 0, [], -1.0
        for rot in self.settings.get("rotations", [0, 90, 180, 270]):
            if progress:
                progress("PP-OCR: поворот %d°" % rot)
            lines = self._read(rotate(v.image, rot))
            w = sum(c * _alnum(t) for t, c in lines)
            if w > best_w:
                best_rot, best_lines, best_w = rot, lines, w
        name = "%s_rot%d" % (v.name, best_rot)
        result.lines = [OcrLine(text=t, confidence=c * 100.0, variant=name) for t, c in best_lines]
        result.best_text = "\n".join(t for t, _c in best_lines)
        result.best_variant = name
        return result
