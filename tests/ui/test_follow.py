# -*- coding: utf-8 -*-
"""Шаг 7.4c: «живое распознавание» — список и карточка чипа идут за фото, которое сейчас проверяется."""
import io
import json
import os
import threading

import pytest

pytest.importorskip("PyQt5")

from test_latency import _pump  # noqa: E402
from test_models import _png  # noqa: E402
from test_photo_cards import _report  # noqa: E402

TOTAL, QUEUE = 12, 5          # в списке 12 фото, первые 7 уже распознаны — в очереди последние 5 (за краем списка)


def _green(i):
    return 20 * i             # у каждого фото свой цвет: по нему видно, какое показано в карточке


def _prepare(window, tmp_path, monkeypatch):
    """Окно на экране, 12 фото, очередь из 5; подменённый конвейер ждёт разрешения на каждое фото."""
    w, app = window
    w.resize(1280, 820)
    w.show()
    paths = [os.path.normpath(_png(str(tmp_path / (u"фото_%02d.png" % i)), (0, _green(i), 255 - _green(i))))
             for i in range(TOTAL)]
    w.add_files(paths)
    _pump(app, lambda: w.list.count() == TOTAL and w.is_idle())
    for p in paths[:TOTAL - QUEUE]:
        w._analyzed((p, _report(p, "OLD", "no"), []))
    _pump(app, lambda: w.is_idle() and w.img_label.image() is not None)
    w.list.clearSelection()       # выделенное фото распознавалось бы повторно; текущим остаётся первое
    step = threading.Semaphore(0)

    def analyze(path, progress=None, marking_override="", ocr_mode=""):
        assert step.acquire(timeout=20)
        return _report(path, "PART%d" % paths.index(path), "yes"), []
    monkeypatch.setattr(w.pipe, "analyze_image", analyze)
    return w, app, paths, step


def _shown(w):
    """Номер фото, показанного в карточке (по цвету); None — картинки нет."""
    image = w.img_label.image()
    return None if image is None else image.pixelColor(5, 5).green() // 20


def _visible(w, row):
    rect = w.list.visualRect(w.photos.index(row))
    return rect.top() >= 0 and rect.bottom() <= w.list.viewport().height()


def test_list_and_card_follow_the_queue(window, tmp_path, monkeypatch):
    w, app, paths, step = _prepare(window, tmp_path, monkeypatch)
    queue = paths[TOTAL - QUEUE:]
    assert w.current_path() == paths[0] and not _visible(w, TOTAL - 1)
    blanks, scrolls = [], []
    set_text = w.img_label.setText
    monkeypatch.setattr(w.img_label, "setText", lambda text: (blanks.append(text), set_text(text)))
    w.list.verticalScrollBar().valueChanged.connect(lambda v: w.list.scrolling() and scrolls.append(v))
    w.run_selected()
    assert w.follow and w.b_follow.isChecked()
    for n, p in enumerate(queue):
        row = paths.index(p)
        _pump(app, lambda: w.photos.status(p)[0] == "busy" and w.current_path() == p)
        assert w.list.selected_paths() == [p]
        _pump(app, lambda: _shown(w) == row and not w.list.scrolling())        # фото перелистнулось, список доехал
        assert _visible(w, row) and w.card.title.text() == os.path.basename(p)
        assert w.card.tag.state() == "busy"
        if n + 1 < len(queue):
            _pump(app, lambda: queue[n + 1] in w._previews)                    # следующее прочитано заранее
        step.release()
        _pump(app, lambda: w.photos.status(p)[0] == "memory")
        assert w.card.title.text() == "PART%d" % row                           # партномер появился на глазах
    _pump(app, lambda: w.is_idle() and not w.queue)
    assert w.current_path() == queue[-1] and _shown(w) == TOTAL - 1            # остаётся последнее показанное
    assert blanks == [], blanks                                                # ни одного пустого кадра «…»
    assert len(set(scrolls)) > 3, scrolls                                      # прокрутка плавная, не скачком
    assert scrolls == sorted(scrolls)


def test_user_stops_following_and_returns(window, tmp_path, monkeypatch):
    from PyQt5.QtCore import QPoint, QPointF, Qt
    from PyQt5.QtGui import QWheelEvent
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
    w, app, paths, step = _prepare(window, tmp_path, monkeypatch)
    queue = paths[TOTAL - QUEUE:]

    def busy(n):
        _pump(app, lambda: w.photos.status(queue[n])[0] == "busy")

    def following(n):
        _pump(app, lambda: w.current_path() == queue[n] and not w.list.scrolling())

    w.run_selected()
    following(0)
    w.list.verticalScrollBar().setValue(0)
    QTest.mouseClick(w.list.viewport(), Qt.LeftButton, pos=w.list.visualRect(w.photos.index(1)).center())
    assert not w.follow and not w.b_follow.isChecked() and w.current_path() == paths[1]
    step.release()
    busy(1)
    _pump(app, lambda: _shown(w) == 1)
    assert w.current_path() == paths[1] and w.list.verticalScrollBar().value() == 0   # выбор пользователя не перебит

    w.b_follow.click()                                       # «Следить» — сразу к фото, которое в работе
    assert w.follow
    following(1)
    w.marking.setFocus()
    QTest.keyClick(w.marking, Qt.Key_A)                      # начал править маркировку
    assert not w.follow and not w.b_follow.isChecked()
    step.release()
    busy(2)
    assert w.current_path() == queue[1]

    w.b_follow.click()
    following(2)
    pos = QPointF(w.list.viewport().rect().center())
    wheel = QWheelEvent(pos, QPointF(w.list.viewport().mapToGlobal(pos.toPoint())), QPoint(0, 120), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(w.list.viewport(), wheel)         # прокрутил список колесом
    assert not w.follow
    step.release()
    busy(3)
    assert w.current_path() == queue[2]

    w.b_follow.click()
    following(3)
    w.part_box.setFocus()
    QTest.keyClick(w.part_box.lineEdit(), Qt.Key_X)          # правит партномер
    assert not w.follow
    step.release()
    step.release()
    _pump(app, lambda: w.is_idle() and not w.queue)
    assert w.current_path() == queue[3]
    assert [w.photos.status(p)[0] for p in queue] == ["memory"] * QUEUE


def test_follow_switched_off_in_settings(window, tmp_path, monkeypatch):
    from digger.gui.dialogs import SettingsDialog
    w, app = window
    cfg_path = os.path.join(w.app_dir, "config.json")
    assert w.ctx.config["ui"]["follow_recognition"] is True              # по умолчанию включено
    dlg = SettingsDialog(w.ctx, w)
    assert dlg.follow.isChecked()
    dlg.follow.setChecked(False)
    dlg._save()
    with io.open(cfg_path, encoding="utf-8") as f:
        assert json.load(f)["ui"] == {"follow_recognition": False, "theme": "light"}
    w._load_context()
    w, app, paths, step = _prepare(window, tmp_path, monkeypatch)
    w.run_selected()
    assert not w.follow and not w.b_follow.isChecked()
    for _ in range(QUEUE):
        step.release()
    _pump(app, lambda: w.is_idle() and not w.queue and w.photos.status(paths[-1])[0] == "memory")
    assert w.current_path() == paths[0] and _shown(w) == 0               # выбор не менялся
    assert w.list.verticalScrollBar().value() == 0
