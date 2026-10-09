# -*- coding: utf-8 -*-
"""Мелкие вспомогательные функции."""
from __future__ import annotations

import os
import re

import numpy as np


def imread(path: str):
    """cv2.imread не понимает русские буквы в пути на Windows — читаем через numpy."""
    import cv2
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Не удалось открыть изображение: %s" % path)
    return img


def imwrite(path: str, img) -> None:
    import cv2
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError("Не удалось сохранить %s" % path)
    buf.tofile(path)


def norm_part(s: str) -> str:
    """Нормализация партномера для сравнения: верхний регистр, без пробелов/дефисов/точек."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def safe_filename(s: str, maxlen: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s or "").strip("._")
    return s[:maxlen] or "file"
