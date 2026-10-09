# -*- coding: utf-8 -*-
"""Интерфейс провайдера распознавания (ARCHITECTURE §6.1).

Новый способ распознавания = папка `recognition/providers/<id>/` (встроенный) или `plugins/ocr_<имя>/`
(сторонний) с классом `Provider(OcrProvider)`. В папке стороннего два файла:
  provider.json  {"id": "mine", "title": "Мой способ", "kind": "local"}   — id: строчные латинские, цифры, _
  provider.py    class Provider(OcrProvider): ...
Провайдер работает, когда его id стоит в `config → recognition.chain`; его настройки —
`recognition.providers.<id>`. Код провайдера вне цепочки не выполняется. `kind: "cloud"` — провайдер
вызывается только с согласия пользователя.
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
