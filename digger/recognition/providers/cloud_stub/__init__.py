# -*- coding: utf-8 -*-
"""Заглушка облачного распознавания: место для будущего сервиса. Всегда «не настроено», в сеть не ходит.

Папку можно удалить — программа её не упоминает. Настоящий облачный провайдер делается так же
(`kind = "cloud"`), сеть — только через `core/netsafe.py`, отправляется только вырезка чипа.
"""
from __future__ import annotations

from typing import Tuple

from ...api import OcrProvider
from ....core.models import OcrResult


class Provider(OcrProvider):
    id = "cloud_stub"
    title = "Облачное распознавание"
    kind = "cloud"
    wants_original = True

    def is_available(self) -> Tuple[bool, str]:
        return False, "не настроено"

    def recognize(self, variants, hints=None, progress=None) -> OcrResult:
        raise RuntimeError("облачное распознавание не настроено")
