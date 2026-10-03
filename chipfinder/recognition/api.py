# -*- coding: utf-8 -*-
"""Интерфейс провайдера распознавания (ARCHITECTURE §6.1).

Новый способ распознавания = папка `recognition/providers/<id>/` или `plugins/ocr_<id>/provider.py`
с классом `Provider(OcrProvider)`. Менеджер находит провайдера по id из `config → recognition.chain`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.interfaces import Context, ProgressFn
from ..core.models import ImageVariant, OcrResult


class OcrProvider:
    id = "provider"
    title = "Провайдер"
    kind = "local"                 # local | cloud
    languages: Tuple[str, ...] = ("en",)
    wants_original = False         # True — получает исходное фото, а не варианты от enhancer

    def __init__(self, settings: Dict[str, Any], ctx: Context) -> None:
        self.settings = settings or {}
        self.ctx = ctx

    def is_available(self) -> Tuple[bool, str]:
        """(доступен, причина если нет). Быстро: без загрузки моделей."""
        return True, ""

    def recognize(self, variants: List[ImageVariant], hints: Optional[Dict[str, Any]] = None,
                  progress: Optional[ProgressFn] = None) -> OcrResult:
        raise NotImplementedError

    def close(self) -> None:
        pass
