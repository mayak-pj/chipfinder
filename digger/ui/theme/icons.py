# -*- coding: utf-8 -*-
"""Иконки: векторный набор Lucide (лицензия ISC — `assets/icons/LICENSE.txt`), перекрашивается под тему.

В файлах набора цвет линий — `currentColor`; при загрузке он заменяется цветом из токенов.
"""
from __future__ import annotations

import io
import os
from typing import Dict, List, Tuple

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons")
LICENSE_FILE = "LICENSE.txt"

_cache: Dict[Tuple[str, str, str, int, int], object] = {}


def names() -> List[str]:
    return sorted(f[:-4] for f in os.listdir(ICON_DIR) if f.endswith(".svg"))


def svg_bytes(name: str, color: str) -> bytes:
    """Текст иконки с подставленным цветом. Нет такой иконки → KeyError."""
    path = os.path.join(ICON_DIR, name + ".svg")
    if not os.path.isfile(path):
        raise KeyError("нет иконки: %s" % name)
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read().replace("currentColor", color).encode("utf-8")


def pixmap(name: str, color: str, size: int, scale: float = 2.0, pad: int = 0):
    """Иконка как QPixmap; `scale` — запас по точкам для экранов с масштабом, `pad` — пустое поле справа."""
    from PyQt5.QtCore import QByteArray, QRectF, Qt
    from PyQt5.QtGui import QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer
    side = max(1, int(round(size * scale)))
    pm = QPixmap(side + int(round(pad * scale)), side)
    pm.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg_bytes(name, color)))
    p = QPainter(pm)
    renderer.render(p, QRectF(0, 0, side, side))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str, disabled: str = "", size: int = 20, pad: int = 0):
    """QIcon в цвете темы; для выключенного состояния — цвет `disabled`; `pad` — отступ до подписи."""
    from PyQt5.QtGui import QIcon
    key = (name, color, disabled, size, pad)
    if key not in _cache:
        ic = QIcon()
        ic.addPixmap(pixmap(name, color, size, pad=pad), QIcon.Normal)
        if disabled:
            ic.addPixmap(pixmap(name, disabled, size, pad=pad), QIcon.Disabled)
        _cache[key] = ic
    return _cache[key]


def write_colored(name: str, color: str, out_dir: str) -> str:
    """Перекрашенная иконка файлом (для `image: url(...)` в QSS). Путь — с прямыми косыми чертами."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s_%s.svg" % (name, color.lstrip("#")))
    data = svg_bytes(name, color)
    if not os.path.isfile(path) or os.path.getsize(path) != len(data):
        with open(path, "wb") as f:
            f.write(data)
    return path.replace("\\", "/")
