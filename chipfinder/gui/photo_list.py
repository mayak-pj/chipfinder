# -*- coding: utf-8 -*-
"""Список фото карточками (ARCHITECTURE §5): миниатюра, партномер, имя файла, цветная метка состояния.

Данные — `PhotoListModel`; здесь только вид и отрисовка. Цвета и размеры — из действующей темы.
"""
from __future__ import annotations

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from ..ui import theme as ui_theme
from ..ui.theme.tokens import SPACE
from .models import NAME_ROLE, PART_ROLE, PHOTO_STATES, STATE_ROLE, THUMB

CARD_GAP = SPACE["sm"]                                   # просвет между карточками
CARD_HEIGHT = THUMB + 2 * SPACE["sm"] + CARD_GAP
TAG_ALPHA = 36                                           # прозрачность фона метки (цвет — тот же, что у подписи)


class PhotoCardDelegate(QStyledItemDelegate):
    """Рисует строку списка карточкой: слева миниатюра, справа партномер (или имя файла), имя файла и метка."""

    def sizeHint(self, option, index):
        return QSize(THUMB * 2, CARD_HEIGHT)

    def paint(self, painter, option, index):
        theme = ui_theme.current()
        t = theme.t
        pad = int(t["space_sm"])
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        card = QRectF(option.rect).adjusted(0.5, CARD_GAP / 2.0 + 0.5, -0.5, -CARD_GAP / 2.0 - 0.5)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(theme.qcolor("accent" if selected else "border"), 1))
        painter.setBrush(theme.qcolor("accent_soft" if selected else ("surface_alt" if hover else "surface")))
        painter.drawRoundedRect(card, t["radius_md"], t["radius_md"])

        box = QRectF(card.left() + pad, card.center().y() - THUMB / 2.0, THUMB, THUMB)
        clip = QPainterPath()
        clip.addRoundedRect(box, t["radius_sm"], t["radius_sm"])
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(box, theme.qcolor("bg"))
        icon = index.data(Qt.DecorationRole)
        if icon is not None:
            icon.paint(painter, box.toRect(), Qt.AlignCenter)
        painter.restore()

        part, name, state = index.data(PART_ROLE) or "", index.data(NAME_ROLE) or "", index.data(STATE_ROLE) or "new"
        left = box.right() + pad
        width = card.right() - pad - left
        bold = QFont(option.font)
        bold.setPixelSize(int(t["font_body"]))
        bold.setBold(True)
        small = QFont(option.font)
        small.setPixelSize(int(t["font_small"]))
        fm_bold, fm_small = QFontMetrics(bold), QFontMetrics(small)
        y = box.top()
        painter.setFont(bold)
        painter.setPen(theme.qcolor("text"))
        title = fm_bold.elidedText(part or name, Qt.ElideRight if part else Qt.ElideMiddle, int(width))
        painter.drawText(QRectF(left, y, width, fm_bold.height()), Qt.AlignLeft | Qt.AlignVCenter, title)
        if part:                                         # имя файла — второй строкой, когда первая занята партномером
            y += fm_bold.height() + 2
            painter.setFont(small)
            painter.setPen(theme.qcolor("text_muted"))
            painter.drawText(QRectF(left, y, width, fm_small.height()), Qt.AlignLeft | Qt.AlignVCenter,
                             fm_small.elidedText(name, Qt.ElideMiddle, int(width)))
        tag, color_key = PHOTO_STATES.get(state, PHOTO_STATES["new"])
        if tag:
            color = theme.qcolor(color_key)
            h = fm_small.height() + 4
            w = min(width, fm_small.horizontalAdvance(tag) + 2 * pad)
            pill = QRectF(left, box.bottom() - h, w, h)
            back = QColor(color)
            back.setAlpha(TAG_ALPHA)
            painter.setPen(Qt.NoPen)
            painter.setBrush(back)
            painter.drawRoundedRect(pill, h / 2.0, h / 2.0)
            painter.setFont(small)
            painter.setPen(color)
            painter.drawText(pill, Qt.AlignCenter, fm_small.elidedText(tag, Qt.ElideRight, int(w - pad)))
        painter.restore()


class PhotoList(QListView):
    """Список фото (модель — `PhotoListModel`); принимает перетащенные файлы и папки."""

    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self.on_files = on_files
        self.setObjectName("photoCards")
        self.setItemDelegate(PhotoCardDelegate(self))
        self.setAcceptDrops(True)
        self.setMouseTracking(True)                      # подсветка карточки под указателем
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(260)

    def count(self) -> int:
        return self.model().rowCount() if self.model() else 0

    def currentRow(self) -> int:
        return self.currentIndex().row()

    def setCurrentRow(self, row: int) -> None:
        self.setCurrentIndex(self.model().index(row, 0))

    def selected_paths(self):
        return [i.data(Qt.UserRole) for i in sorted(self.selectionModel().selectedIndexes(), key=lambda i: i.row())]

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.on_files(paths)
        e.acceptProposedAction()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:                            # пустой список — место, куда перетаскивать
            theme = ui_theme.current()
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(theme.qcolor("border_strong"), 1, Qt.DashLine))
            zone = QRectF(self.viewport().rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(zone, theme.t["radius_md"], theme.t["radius_md"])
            p.setPen(theme.qcolor("text_muted"))
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       "Перетащите сюда\nвырезанные фото\nмикросхем\n(файлы или папку)")
