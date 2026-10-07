# -*- coding: utf-8 -*-
"""Шаг 7.4a: список фото карточками — состояние фото в модели, отрисовка карточки, метка «в работе»."""
import os
import threading

import pytest

pytest.importorskip("PyQt5")

from chipfinder.core.models import ChipReport, MemoryVerdict  # noqa: E402
from test_latency import _pump  # noqa: E402
from test_models import _png  # noqa: E402


def _report(path, part="", memory=None, datasheet=""):
    r = ChipReport(image_path=path)
    r.chosen_part = part
    r.datasheet_path = datasheet
    if memory is not None:
        r.memory = MemoryVerdict(has_memory={"yes": True, "no": False, "?": None}[memory])
    return r


def _near(image, color, tolerance=40):
    """Сколько точек картинки близки к цвету (текст сглажен — точного совпадения ждать нельзя)."""
    n = 0
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if abs(c.red() - color.red()) + abs(c.green() - color.green()) + abs(c.blue() - color.blue()) <= tolerance:
                n += 1
    return n


def test_photo_status_in_model(qapp):
    from PyQt5.QtCore import Qt
    from chipfinder.gui.models import NAME_ROLE, PART_ROLE, PHOTO_STATES, STATE_ROLE, PhotoListModel
    m = PhotoListModel()
    a, b = os.path.join("d", "a.png"), os.path.join("d", "b.png")
    m.add([a, b])
    changed = []
    m.dataChanged.connect(lambda first, last, _roles=None: changed.append((first.row(), last.row())))
    idx = m.index(0)
    assert (idx.data(NAME_ROLE), idx.data(PART_ROLE), idx.data(STATE_ROLE)) == ("a.png", "", "new")
    assert idx.data(Qt.DisplayRole) == "a.png" and m.status(a) == ("new", "")
    m.set_status(a, "busy")
    assert idx.data(Qt.DisplayRole) == u"a.png\nраспознаю…" and changed == [(0, 0)]
    m.set_status(a, "memory", "W25Q64JV")
    assert idx.data(Qt.DisplayRole) == u"a.png\nW25Q64JV — память" and idx.data(PART_ROLE) == "W25Q64JV"
    m.set_status(a, "search")                                # партномер не назван — остаётся прежний
    assert m.status(a) == ("search", "W25Q64JV") and idx.data(Qt.DisplayRole) == u"a.png\nW25Q64JV — ищу…"
    m.set_status(a, "search")                                # то же самое — вид не перерисовывается
    assert len(changed) == 3
    m.set_status("чужой.png", "busy")                        # фото уже убрали из списка — не ошибка
    assert m.index(1).data(STATE_ROLE) == "new" and len(changed) == 3
    with pytest.raises(KeyError):
        m.set_status(a, "нет такого")
    m.remove([a])
    assert m.add([a]) == [a] and m.status(a) == ("new", "")  # убрали и добавили снова — как новое
    for title, color in PHOTO_STATES.values():               # у каждой метки есть цвет из токенов темы
        assert color in __import__("chipfinder.ui.theme.tokens", fromlist=["LIGHT"]).LIGHT


def test_cards_are_painted(qapp, tmp_path):
    from PyQt5.QtCore import QItemSelectionModel, QRect
    from PyQt5.QtGui import QImage
    from chipfinder.gui.models import PhotoListModel
    from chipfinder.gui.photo_list import CARD_HEIGHT, PhotoList
    from chipfinder.ui import theme as ui_theme
    theme = ui_theme.apply_theme(qapp, "light", str(tmp_path / "theme"))
    try:
        m = PhotoListModel(theme.icon("cpu", "text_disabled", 72))
        lst = PhotoList(lambda paths: None)
        lst.setModel(m)
        lst.resize(290, 420)
        paths = [os.path.join(u"папка", u"очень_длинное_имя_файла_с_фотографией_микросхемы_%d.png" % i) for i in range(3)]
        m.add(paths)
        qi = QImage(64, 48, QImage.Format_RGB888)
        qi.fill(theme.qcolor("success"))
        m.set_thumbs([(paths[0], qi)])
        r0, r1 = lst.visualRect(m.index(0)), lst.visualRect(m.index(1))
        assert r0.height() == CARD_HEIGHT and r1.top() - r0.top() == CARD_HEIGHT
        assert r0.width() <= lst.viewport().width()          # карточка не шире списка: длинное имя сокращается

        def card(row):
            rect = lst.visualRect(m.index(row))
            return lst.viewport().grab(QRect(rect.topLeft(), rect.size())).toImage()
        danger, ok = theme.qcolor("danger"), theme.qcolor("success")
        new = card(1)
        assert _near(new, danger) == 0
        m.set_status(paths[1], "memory", "W25Q64JV")
        with_memory = card(1)
        assert with_memory != new and _near(with_memory, danger) > 10        # красная метка «память»
        m.set_status(paths[1], "no_memory", "LM358")
        without = card(1)
        assert _near(without, danger) == 0 and _near(without, ok) > 10       # зелёная «без памяти»
        assert _near(card(0), ok) > 500                                      # миниатюра на карточке
        lst.selectionModel().select(m.index(1), QItemSelectionModel.Select)
        assert card(1) != without                                            # выбранная карточка выделена
        ui_theme.apply_theme(qapp, "dark", str(tmp_path / "theme"))          # цвета берутся из действующей темы
        assert card(2) != new
    finally:
        ui_theme.apply_theme(qapp, "light", str(tmp_path / "theme"))


def test_window_marks_photo_in_work(window, tmp_path, monkeypatch):
    w, app = window
    a, b = [os.path.normpath(_png(str(tmp_path / n))) for n in ("a.png", "b.png")]
    w.add_files([a, b])
    _pump(app, lambda: w.list.count() == 2 and w.is_idle())
    go = threading.Event()

    def analyze(path, progress=None, marking_override="", ocr_mode=""):
        assert go.wait(10)
        if path == a:
            return _report(path, "W25Q64JV", "yes"), []
        return _report(path, "LM358", "no"), []
    monkeypatch.setattr(w.pipe, "analyze_image", analyze)
    w.run_selected()
    _pump(app, lambda: w.photos.status(a)[0] == "busy")
    assert w.photos.status(b) == ("new", "")                 # очередь до него ещё не дошла
    go.set()
    _pump(app, lambda: w.is_idle() and not w.queue and w.photos.status(b)[0] != "busy")
    assert w.photos.status(a) == ("memory", "W25Q64JV") and w.photos.status(b) == ("no_memory", "LM358")

    stop = threading.Event()

    def search(r, progress=None, cancel=None, levels=None):
        assert stop.wait(10)
    monkeypatch.setattr(w.pipe, "search_web", search)
    w.list.setCurrentRow(1)
    w.web_search(None)
    _pump(app, lambda: w.photos.status(b) == ("search", "LM358"))
    stop.set()
    _pump(app, lambda: w.is_idle() and w.photos.status(b) == ("no_memory", "LM358"))


def test_window_states_from_report_and_after_failure(window, tmp_path, monkeypatch):
    w, app = window
    paths = [os.path.normpath(_png(str(tmp_path / ("p%d.png" % i)))) for i in range(4)]
    w.add_files(paths)
    _pump(app, lambda: w.list.count() == 4 and w.is_idle())
    w._analyzed((paths[0], _report(paths[0], "NE555", "?"), []))                       # документа нет
    w._analyzed((paths[1], _report(paths[1], "NE555", "?", datasheet="ne555.pdf"), []))  # документ есть, вывода нет
    w._analyzed((paths[2], _report(paths[2]), []))                                     # маркировка не прочитана
    assert [w.photos.status(p)[0] for p in paths] == ["not_found", "unknown", "unread", "new"]

    def broken(path, progress=None, marking_override="", ocr_mode=""):
        raise RuntimeError(u"сбой распознавания")
    monkeypatch.setattr(w.pipe, "analyze_image", broken)
    w.list.setCurrentRow(3)
    w.run_selected()
    _pump(app, lambda: w.is_idle() and u"сбой распознавания" in w.log_view.toPlainText())
    assert w.photos.status(paths[3]) == ("new", "")          # метка «распознаю…» не осталась висеть
