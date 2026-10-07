# -*- coding: utf-8 -*-
"""Тема окна (ARCHITECTURE §5): стиль Fusion + палитра + QSS + шрифты + иконки — всё из токенов.

    theme = apply_theme(app, "light", cache_dir)    # один раз при запуске (и при смене темы)
    action.setIcon(theme.icon("search"))
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Optional

from . import fonts, icons, tokens
from .qss import build_qss
from .tokens import DEFAULT_THEME, THEMES, theme_name

__all__ = ["Theme", "apply_theme", "current", "configured_theme", "DEFAULT_THEME", "THEMES"]

QSS_IMAGES = ("chevron-down", "chevron-up")      # стрелки списков и счётчиков — файлами: QSS берёт картинки по пути

_current: Optional[Theme] = None


class Theme(object):
    def __init__(self, name: str = DEFAULT_THEME):
        self.name = theme_name(name)
        self.t = tokens.tokens(self.name)
        self.qss = ""
        self.info: Dict[str, object] = {}      # диагностика: шрифты, картинки QSS

    def color(self, key: str) -> str:
        return str(self.t[key])

    def qcolor(self, key: str):
        from PyQt5.QtGui import QColor
        return QColor(self.color(key))

    def icon(self, name: str, color: str = "icon", size: int = 0, pad: int = 0):
        return icons.icon(name, self.color(color), self.color("text_disabled"), size or int(self.t["icon_size_md"]), pad)

    def pixmap(self, name: str, color: str = "icon", size: int = 0):
        return icons.pixmap(name, self.color(color), size or int(self.t["icon_size_md"]))

    def palette(self):
        """Палитра Fusion: всё, что рисуется не через QSS (текст элементов, ссылки, подсказки в полях)."""
        from PyQt5.QtGui import QPalette
        p = QPalette()
        for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
                          (QPalette.AlternateBase, "bg"), (QPalette.Text, "text"), (QPalette.Button, "surface"),
                          (QPalette.ButtonText, "text"), (QPalette.BrightText, "on_accent"),
                          (QPalette.Highlight, "accent_soft"), (QPalette.HighlightedText, "text"),
                          (QPalette.ToolTipBase, "text"), (QPalette.ToolTipText, "surface"),
                          (QPalette.Link, "accent"), (QPalette.Mid, "border"), (QPalette.Dark, "border_strong")):
            p.setColor(role, self.qcolor(key))
        if hasattr(QPalette, "PlaceholderText"):
            p.setColor(QPalette.PlaceholderText, self.qcolor("text_muted"))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            p.setColor(QPalette.Disabled, role, self.qcolor("text_disabled"))
        return p


def configured_theme(app_dir: str) -> str:
    """Тема из настроек программы (`ui.theme`); настроек нет или они не читаются → тема по умолчанию."""
    try:
        from ...core.config import load_config
        return theme_name(str(load_config(app_dir).get("ui", {}).get("theme", "")))
    except Exception:  # noqa — тема не должна мешать запуску
        return DEFAULT_THEME


def apply_theme(app, name: str = DEFAULT_THEME, cache_dir: str = "") -> Theme:
    """Применить тему ко всему приложению. `cache_dir` — куда положить перекрашенные стрелки для QSS."""
    global _current
    from PyQt5.QtGui import QPixmap
    theme = Theme(name)
    cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "chipfinder_theme")
    images = {}
    for icon_name in QSS_IMAGES:
        try:
            images[icon_name.replace("-", "_")] = icons.write_colored(icon_name, theme.color("text_muted"), cache_dir)
        except (OSError, KeyError) as e:      # папка недоступна — тема остаётся, только без стрелок
            images[icon_name.replace("-", "_")] = ""
            theme.info["image_error"] = "%s: %s" % (type(e).__name__, e)
    theme.qss = build_qss(theme.t, images)
    font, font_info = fonts.app_font(int(theme.t["font_body"]))
    style = app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(font)
    app.setStyleSheet(theme.qss)
    theme.info.update(theme=theme.name, style=style.objectName() if style else "", fonts=font_info, icons=len(icons.names()),
                      qss_images={k: bool(v) and not QPixmap(v).isNull() for k, v in images.items()})
    _current = theme
    return theme


def current() -> Theme:
    """Действующая тема; до `apply_theme` — светлая (цвета и иконки доступны, приложение не трогается)."""
    return _current or Theme(DEFAULT_THEME)
