# -*- coding: utf-8 -*-
"""QSS темы из токенов. Цвета и размеры — только подстановками `$имя`; своих чисел цвета здесь нет."""
from __future__ import annotations

from string import Template
from typing import Dict

TEMPLATE = u"""
QWidget { color: $text; font-size: ${font_body}px; }
QMainWindow, QDialog, QDockWidget, QStatusBar, QMenuBar, QToolBar { background: $bg; }
QWidget:disabled { color: $text_disabled; }
QToolTip { background: $text; color: $surface; border: none; padding: ${space_xs}px ${space_sm}px; }

QMenuBar { border-bottom: 1px solid $border; padding: 2px ${space_xs}px; }
QMenuBar::item { background: transparent; padding: ${space_xs}px ${space_sm}px; border-radius: ${radius_sm}px; }
QMenuBar::item:selected, QMenuBar::item:pressed { background: $surface_alt; }
QMenu { background: $surface; border: 1px solid $border; padding: ${space_xs}px; }
QMenu::item { padding: ${space_xs}px ${space_xl}px ${space_xs}px ${space_md}px; border-radius: ${radius_sm}px; }
QMenu::item:selected { background: $accent_soft; color: $text; }
QMenu::separator { height: 1px; background: $border; margin: ${space_xs}px ${space_sm}px; }

QToolBar { border: none; border-bottom: 1px solid $border; padding: ${space_xs}px ${space_sm}px; spacing: ${space_xs}px; }
QToolBar::separator { width: 1px; background: $border; margin: ${space_xs}px ${space_sm}px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: ${radius_sm}px;
              padding: ${space_xs}px ${space_sm}px; }
QToolButton:hover { background: $surface_alt; }
QToolButton:pressed, QToolButton:checked { background: $accent_soft; }

QPushButton { background: $surface; border: 1px solid $border; border-radius: ${radius_sm}px;
              padding: 0 ${space_md}px; min-height: ${button_inner}px; }
QPushButton:hover { background: $surface_alt; border-color: $border_strong; }
QPushButton:pressed { background: $accent_soft; }
QPushButton:checked { background: $accent_soft; border-color: $accent; }
QPushButton:default, QPushButton[accent="true"] { background: $accent; border-color: $accent; color: $on_accent; }
QPushButton:default:hover, QPushButton[accent="true"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton:default:pressed, QPushButton[accent="true"]:pressed { background: $accent_pressed; }
QPushButton[iconOnly="true"] { padding: 0; min-width: ${button_inner}px; max-width: ${button_inner}px; }
QPushButton:disabled { background: $bg; border-color: $border; color: $text_disabled; }

QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox, QListView, QTableView, QTreeView {
    background: $surface; border: 1px solid $border; border-radius: ${radius_sm}px;
    selection-background-color: $accent_soft; selection-color: $text; }
QLineEdit, QSpinBox, QComboBox { min-height: ${button_inner}px; padding: 0 ${space_sm}px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled { background: $bg; }

QComboBox::drop-down { border: none; width: ${space_xl}px; }
QComboBox::down-arrow { image: url("$img_chevron_down"); width: ${icon_size_sm}px; height: ${icon_size_sm}px; }
QComboBox QAbstractItemView { background: $surface; border: 1px solid $border; outline: none; }
QSpinBox::up-button, QSpinBox::down-button { border: none; width: ${space_lg}px; }
QSpinBox::up-arrow { image: url("$img_chevron_up"); width: ${space_md}px; height: ${space_md}px; }
QSpinBox::down-arrow { image: url("$img_chevron_down"); width: ${space_md}px; height: ${space_md}px; }

QListView, QTableView, QTreeView { outline: none; alternate-background-color: $bg; gridline-color: $border; }
QListView::item { padding: ${space_xs}px; border-radius: ${radius_sm}px; }
QListView::item:hover, QTableView::item:hover { background: $surface_alt; }
QListView::item:selected, QTableView::item:selected, QTreeView::item:selected { background: $accent_soft; color: $text; }
QListView#photoCards { background: transparent; border: none; }
QHeaderView::section { background: $surface_alt; color: $text_muted; border: none; border-right: 1px solid $border;
                       border-bottom: 1px solid $border; padding: ${space_xs}px ${space_sm}px; }
QTableCornerButton::section { background: $surface_alt; border: none; }

QGroupBox { background: $surface; border: 1px solid $border; border-radius: ${radius_md}px;
            margin-top: ${space_xl}px; padding: ${space_sm}px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: ${space_xs}px; top: ${space_xs}px;
                   color: $text_muted; font-size: ${font_small}px; font-weight: bold; }

QTabWidget::pane { background: $surface; border: 1px solid $border; border-radius: ${radius_md}px; top: -1px; }
QTabBar::tab { background: transparent; color: $text_muted; border: none; border-bottom: 2px solid transparent;
               padding: ${space_sm}px ${space_lg}px; }
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $accent; border-bottom-color: $accent; }

QScrollBar:vertical { background: transparent; width: ${space_md}px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: ${space_md}px; margin: 0; }
QScrollBar::handle { background: $border_strong; border-radius: 3px; margin: 3px; }
QScrollBar::handle:vertical { min-height: ${space_xl}px; }
QScrollBar::handle:horizontal { min-width: ${space_xl}px; }
QScrollBar::handle:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QProgressBar { background: $surface_alt; border: none; border-radius: 3px; max-height: 6px; text-align: center; }
QProgressBar::chunk { background: $accent; border-radius: 3px; }
QStatusBar { border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QStatusBar QLabel { padding: 0 ${space_sm}px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: ${space_sm}px; }
QSplitter::handle:vertical { height: ${space_sm}px; }
QCheckBox, QRadioButton { spacing: ${space_sm}px; }

QFrame#chipCard { background: $surface; border: 1px solid $border; border-radius: ${radius_md}px; }
QLabel[muted="true"] { color: $text_muted; }
QLabel[role="title"] { font-size: ${font_title}px; font-weight: bold; }
"""


def build_qss(t: Dict[str, object], images: Dict[str, str]) -> str:
    """QSS для темы `t` (см. `tokens.tokens`); `images` — пути к перекрашенным стрелкам (`chevron_down`, …)."""
    values = dict(t)
    values["button_inner"] = int(t["control_height"]) - 2          # высота без рамки
    for k, v in images.items():
        values["img_" + k] = v
    return Template(TEMPLATE).substitute(values)
