# -*- coding: utf-8 -*-
"""Провайдер Tesseract — запасной: обёртка над `modules/ocr_tesseract.py` (читает варианты от enhancer)."""
from __future__ import annotations

from typing import Tuple

from ...api import OcrProvider
from ....core.models import OcrResult
from ....modules.ocr_tesseract import TesseractOCR


class Provider(OcrProvider):
    id = "tesseract"
    title = "Tesseract"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        # путь к tesseract.exe окно «Настройки» пишет в module_settings.ocr
        merged = dict(ctx.config.get("module_settings", {}).get("ocr", {}))
        merged.update(self.settings)
        self._impl = TesseractOCR(merged, ctx)

    def is_available(self) -> Tuple[bool, str]:
        return self._impl.is_available(), self._impl.error

    def recognize(self, variants, hints=None, progress=None) -> OcrResult:
        return self._impl.recognize(variants, progress=progress)
