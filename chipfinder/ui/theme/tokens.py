# -*- coding: utf-8 -*-
"""Дизайн-токены (ARCHITECTURE §5): цвета, отступы, скругления, размеры шрифтов. Без Qt.

Обе темы описаны одним набором имён: QSS, палитра и иконки берут цвета только отсюда.
"""
from __future__ import annotations

from typing import Dict

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = {"sm": 6, "md": 10}
FONT_SIZE = {"small": 12, "body": 13, "title": 15}       # пиксели: одинаково на Win7 и на Mac
ICON_SIZE = {"sm": 16, "md": 20}
CONTROL_HEIGHT = 30

LIGHT = {
    "bg": "#f4f6f9",              # фон окна
    "surface": "#ffffff",         # карточки, поля, списки
    "surface_alt": "#eef1f5",     # заголовки таблиц, наведение
    "border": "#d5dae1",
    "border_strong": "#b6bec9",
    "text": "#1c2430",
    "text_muted": "#647082",
    "text_disabled": "#a3acb8",
    "accent": "#2563eb",
    "accent_hover": "#1d4fd8",
    "accent_pressed": "#1a43b8",
    "accent_soft": "#e3ecfd",     # выделенная строка, найденное в библиотеке
    "on_accent": "#ffffff",
    "success": "#1a7f37",
    "warning": "#b26a00",
    "danger": "#c62828",
    "shadow": "rgba(28, 36, 48, 40)",
    "icon": "#465264",
}

DARK = {
    "bg": "#171a1f",
    "surface": "#20242b",
    "surface_alt": "#2a2f38",
    "border": "#363c47",
    "border_strong": "#4a5260",
    "text": "#e6e9ee",
    "text_muted": "#9aa4b2",
    "text_disabled": "#5f6875",
    "accent": "#5b8cff",
    "accent_hover": "#7aa2ff",
    "accent_pressed": "#4a78e6",
    "accent_soft": "#263556",
    "on_accent": "#0e1116",
    "success": "#4cc26b",
    "warning": "#e0a030",
    "danger": "#ff6b6b",
    "shadow": "rgba(0, 0, 0, 100)",
    "icon": "#b4bcc8",
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "light"


def theme_name(name: str) -> str:
    """Имя темы из настроек; неизвестное → тема по умолчанию."""
    return name if name in THEMES else DEFAULT_THEME


def tokens(name: str = DEFAULT_THEME) -> Dict[str, object]:
    """Плоский словарь темы: цвета + `space_*`, `radius_*`, `font_*`, `icon_*`, `control_height`."""
    t: Dict[str, object] = dict(THEMES[theme_name(name)])
    t["name"] = theme_name(name)
    for prefix, group in (("space", SPACE), ("radius", RADIUS), ("font", FONT_SIZE), ("icon_size", ICON_SIZE)):
        for k, v in group.items():
            t["%s_%s" % (prefix, k)] = v
    t["control_height"] = CONTROL_HEIGHT
    return t
