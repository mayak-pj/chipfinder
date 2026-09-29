# -*- coding: utf-8 -*-
"""Главное окно: перетаскивание фото, распознавание, поиск, заключение."""
from __future__ import annotations

import csv
import datetime
import io
import os
import sys

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QApplication, QComboBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
                             QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                             QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolBar,
                             QVBoxLayout, QWidget)

from ..core.config import resolve_path
from ..core.pipeline import ChipPipeline, create_context
from ..core.utils import safe_filename
from .dialogs import DiagnosticsDialog, SettingsDialog
from .worker import Job

IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
PACKAGES = ["", "SOP-8", "SOIC-8", "DIP-8", "TSSOP-8", "MSOP-8", "SOT-23-5", "SOT-23-6", "DFN-8", "WSON-8",
            "SOP-14", "SOP-16", "DIP-14", "DIP-16", "DIP-28", "DIP-40", "SSOP-20", "TSSOP-20", "SSOP-28",
            "QFN-20", "QFN-24", "QFN-32", "QFN-48", "LQFP-32", "LQFP-48", "LQFP-64", "LQFP-100", "LQFP-144",
            "TQFP-32", "TQFP-44", "PLCC-32", "PLCC-44", "TSOP-48", "BGA"]
LEVEL_RU = {"local": "База", "catalog": "Каталог", "maker": "Производитель", "china": "Китай",
            "forum": "Форум", "marking": "SMD-код", "github": "GitHub"}


def np_to_pixmap(img, max_w=520, max_h=300) -> QPixmap:
    import cv2
    if img.ndim == 2:
        h, w = img.shape
        qi = QImage(img.data, w, h, img.strides[0], QImage.Format_Grayscale8).copy()
    else:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        qi = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qi).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class DropList(QListWidget):
    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self.on_files = on_files
        self.setAcceptDrops(True)
        self.setIconSize(QSize(72, 72))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setMinimumWidth(260)
        self.setWordWrap(True)

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
        if self.count() == 0:
            from PyQt5.QtGui import QPainter
            p = QPainter(self.viewport())
            p.setPen(QColor("#888"))
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       "Перетащите сюда\nвырезанные фото\nмикросхем\n(файлы или папку)")


class MainWindow(QMainWindow):
    def __init__(self, app_dir: str):
        super().__init__()
        self.app_dir = app_dir
        self.items = {}          # path -> {"report", "variants"}
        self.job = None
        self.queue = []
        self.ctx = None
        self.pipe = None
        self.setWindowTitle("ChipFinder — поиск datasheet по фото микросхемы")
        self.resize(1280, 820)
        self._build_ui()
        self._load_context()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        tb = QToolBar("Действия")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if tip:
                a.setToolTip(tip)
            tb.addAction(a)
            return a
        act("Добавить фото…", self.add_files_dialog)
        self.a_run = act("Распознать", self.run_selected, "Распознать выбранные (или все новые) фото")
        self.a_web = act("Искать в интернете", lambda: self.web_search(None), "Поиск по уровням до первых хороших результатов")
        self.a_web_all = act("Искать везде", lambda: self.web_search(["all"]), "Пройти все уровни поиска")
        self.a_stop = act("Стоп", self.stop_job)
        tb.addSeparator()
        act("Сохранить отчёт", self.save_report)
        act("Сводка CSV", self.save_summary)

        mb = self.menuBar()
        m = mb.addMenu("Файл")
        m.addAction("Добавить фото…", self.add_files_dialog)
        m.addAction("Сохранить отчёт (HTML)…", self.save_report)
        m.addAction("Сводка по всем фото (CSV для Excel)…", self.save_summary)
        m.addSeparator()
        m.addAction("Выход", self.close)
        m = mb.addMenu("База")
        m.addAction("Индексировать папки с datasheet", self.index_db)
        m.addAction("Импорт каталога деталей (KiCad zip/папка, CSV)…", self.import_catalog)
        m.addAction("Добавить свой PDF в базу для текущего чипа…", self.add_own_pdf)
        m.addAction("Статистика базы", self.db_stats)
        m.addAction("Открыть папку библиотеки", lambda: self._open_path(self.ctx and self.ctx.modules["local_db"].library_dir))
        m = mb.addMenu("Сеть")
        m.addAction("Диагностика: какие сайты доступны", self.diagnose)
        m.addAction("Журнал сетевых обращений", lambda: self._open_path(
            os.path.join(resolve_path(self.app_dir, self.ctx.config["paths"]["log_dir"]), "network_audit.log")))
        m.addAction("Папка карантина", lambda: self._open_path(resolve_path(self.app_dir, self.ctx.config["paths"]["quarantine_dir"])))
        mb.addAction("Настройки…", self.settings)
        m = mb.addMenu("Справка")
        m.addAction("Инструкция (README)", lambda: self._open_path(os.path.join(self.app_dir, "README.md")))
        m.addAction("Модули", self.show_modules)

        split = QSplitter(Qt.Horizontal)
        self.setCentralWidget(split)

        # слева — список фото
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(4, 4, 4, 4)
        self.list = DropList(self.add_files)
        self.list.currentItemChanged.connect(self.show_current)
        ll.addWidget(self.list)
        rm = QPushButton("Убрать из списка")
        rm.clicked.connect(self.remove_selected)
        ll.addWidget(rm)
        split.addWidget(left)

        # справа
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        imgbox = QGroupBox("Изображение")
        il = QVBoxLayout(imgbox)
        self.img_label = QLabel("—")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setMinimumSize(420, 240)
        self.img_label.setStyleSheet("background:#fafafa;border:1px solid #ddd")
        il.addWidget(self.img_label)
        self.variant_box = QComboBox()
        self.variant_box.currentIndexChanged.connect(self.show_variant)
        il.addWidget(self.variant_box)
        top.addWidget(imgbox, 3)

        form_box = QGroupBox("Маркировка и параметры (можно исправить вручную)")
        fl = QFormLayout(form_box)
        self.marking = QPlainTextEdit()
        self.marking.setMaximumHeight(80)
        self.marking.setPlaceholderText("Текст с корпуса, по строкам")
        fl.addRow("Маркировка:", self.marking)
        b = QPushButton("Пересчитать по этому тексту")
        b.clicked.connect(self.reidentify)
        fl.addRow("", b)
        self.part_box = QComboBox()
        self.part_box.setEditable(True)
        self.part_box.setInsertPolicy(QComboBox.NoInsert)
        pb = QPushButton("Выбрать партномер")
        pb.clicked.connect(self.choose_part)
        row = QHBoxLayout()
        row.addWidget(self.part_box, 1)
        row.addWidget(pb)
        fl.addRow("Партномер:", row)
        self.pkg_box = QComboBox()
        self.pkg_box.setEditable(True)
        self.pkg_box.addItems(PACKAGES)
        self.pins = QSpinBox()
        self.pins.setRange(0, 2000)
        self.pins.setSpecialValueText("?")
        ab = QPushButton("Применить")
        ab.clicked.connect(self.apply_chip)
        row2 = QHBoxLayout()
        row2.addWidget(self.pkg_box, 1)
        row2.addWidget(QLabel("выводов:"))
        row2.addWidget(self.pins)
        row2.addWidget(ab)
        fl.addRow("Корпус на фото:", row2)
        top.addWidget(form_box, 4)
        rl.addLayout(top)

        self.tabs = QTabWidget()
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        self.tabs.addTab(self.report_view, "Заключение")

        docs = QWidget()
        dl = QVBoxLayout(docs)
        dl.setContentsMargins(0, 0, 0, 0)
        self.hits = QTableWidget(0, 7)
        self.hits.setHorizontalHeaderLabels(["Где", "Источник", "Название", "Оценка", "PDF", "Адрес", "Примечание"])
        self.hits.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.hits.setSelectionMode(QAbstractItemView.SingleSelection)
        self.hits.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.hits.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.hits.doubleClicked.connect(lambda _i: self.use_hit())
        dl.addWidget(self.hits)
        hb = QHBoxLayout()
        for text, slot in (("Использовать как datasheet", self.use_hit),
                           ("Скачать и проверить", self.download_hit),
                           ("Открыть PDF", self.open_hit_pdf),
                           ("Открыть ссылку в браузере", self.open_hit_browser),
                           ("Копировать адрес", self.copy_hit)):
            bb = QPushButton(text)
            bb.clicked.connect(slot)
            hb.addWidget(bb)
        hb.addStretch()
        dl.addLayout(hb)
        self.tabs.addTab(docs, "Документы")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.tabs.addTab(self.log_view, "Журнал")
        rl.addWidget(self.tabs, 1)
        split.addWidget(right)
        split.setSizes([290, 990])

        self.status = QLabel("Готово")
        self.busy = QProgressBar()
        self.busy.setMaximumWidth(160)
        self.busy.setRange(0, 0)
        self.busy.hide()
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.busy)
        self._set_busy(False)

    # ------------------------------------------------------------ контекст
    def _load_context(self):
        try:
            if self.ctx:
                for mod in self.ctx.modules.values():
                    mod.close()
            self.ctx = create_context(self.app_dir)
            self.pipe = ChipPipeline(self.ctx)
            ocr = self.ctx.modules["ocr"]
            if not ocr.is_available():
                self.log("⚠ " + getattr(ocr, "error", "OCR недоступен"))
                QMessageBox.warning(self, "Tesseract не найден", getattr(ocr, "error", "") +
                                    "\n\nМаркировку можно вводить вручную. Путь к Tesseract задаётся в «Настройках».")
            st = self.ctx.modules["local_db"].stats()
            self.log("База: %(files)d файлов (скачано %(library)d), каталог деталей: %(catalog)d" % st)
            if st["files"] == 0 and self.ctx.config["paths"].get("scan_roots"):
                self.log("Индекс пуст — запустите «База → Индексировать папки»")
        except Exception as e:  # noqa
            QMessageBox.critical(self, "Ошибка запуска модулей", str(e))

    def log(self, msg):
        self.log_view.appendPlainText("%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg))
        self.status.setText(msg[:200])

    def _set_busy(self, busy):
        self.busy.setVisible(busy)
        self.a_stop.setEnabled(busy)
        for a in (self.a_run, self.a_web, self.a_web_all):
            a.setEnabled(not busy)

    def start_job(self, fn, on_done, *args, **kwargs):
        if self.job and self.job.isRunning():
            QMessageBox.information(self, "Подождите", "Уже выполняется задача. Нажмите «Стоп», чтобы прервать.")
            return False
        self.job = Job(fn, *args, **kwargs)
        self.job.progress.connect(self.log)
        self.job.done.connect(on_done)
        self.job.failed.connect(self._job_failed)
        self.job.finished.connect(lambda: self._set_busy(False))
        self._set_busy(True)
        self.job.start()
        return True

    def _job_failed(self, msg):
        self.log("Ошибка: " + msg.splitlines()[0])
        self.ctx.log.error(msg)
        QMessageBox.warning(self, "Ошибка", msg[:2000])
        self.queue = []

    def stop_job(self):
        if self.job and self.job.isRunning():
            self.job.cancel.cancel()
            self.queue = []
            self.log("Останавливаю…")

    # ------------------------------------------------------------ файлы
    def add_files_dialog(self):
        paths = QFileDialog.getOpenFileNames(self, "Фото микросхем", "",
                                             "Изображения (%s)" % " ".join("*" + e for e in IMG_EXT))[0]
        self.add_files(paths)

    def add_files(self, paths):
        added = 0
        for p in paths:
            if os.path.isdir(p):
                for fn in sorted(os.listdir(p)):
                    if fn.lower().endswith(IMG_EXT):
                        added += self._add_one(os.path.join(p, fn))
            elif p.lower().endswith(IMG_EXT):
                added += self._add_one(p)
        if added:
            self.log("Добавлено фото: %d" % added)
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(0)

    def _add_one(self, path):
        path = os.path.normpath(path)
        if path in self.items:
            return 0
        self.items[path] = {"report": None, "variants": []}
        it = QListWidgetItem(QIcon(QPixmap(path).scaled(72, 72, Qt.KeepAspectRatio)), os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.list.addItem(it)
        return 1

    def remove_selected(self):
        for it in self.list.selectedItems():
            self.items.pop(it.data(Qt.UserRole), None)
            self.list.takeItem(self.list.row(it))

    def current_path(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def current(self):
        p = self.current_path()
        return self.items.get(p) if p else None

    # ------------------------------------------------------------ обработка
    def run_selected(self):
        # выделенные (в т.ч. повторно) + все ещё не распознанные
        sel = [it.data(Qt.UserRole) for it in self.list.selectedItems()]
        sel += [p for p, d in self.items.items() if d["report"] is None and p not in sel]
        if not sel:
            self.log("Нет новых фото. Выделите фото, чтобы распознать повторно.")
            return
        self.queue = list(sel)
        self._next_in_queue()

    def _next_in_queue(self):
        if not self.queue:
            return
        if self.job and self.job.isRunning():
            QTimer.singleShot(150, self._next_in_queue)
            return
        path = self.queue.pop(0)

        def work(path, progress, cancel):
            r, variants = self.pipe.analyze_image(path, progress=progress)
            if (self.ctx.config["pipeline"].get("auto_web_search") and not r.datasheet_path
                    and r.chosen_part and not cancel.cancelled):
                self.pipe.search_web(r, progress=progress, cancel=cancel)
            return path, r, variants

        self.start_job(work, self._analyzed, path)

    def _analyzed(self, res):
        path, r, variants = res
        self.items[path] = {"report": r, "variants": variants}
        self._mark_item(path)
        if path == self.current_path():
            self.show_current()
        if self.queue:
            QTimer.singleShot(150, self._next_in_queue)
        else:
            self.log("Готово")

    def _mark_item(self, path):
        d = self.items.get(path)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == path and d and d["report"]:
                r = d["report"]
                mem = r.memory.has_memory if r.memory else None
                tag = {True: "ПАМЯТЬ", False: "без памяти", None: "?"}[mem]
                it.setText("%s\n%s — %s" % (os.path.basename(path), r.chosen_part or "?", tag))
                it.setForeground(QColor("#8a1c1c") if mem else (QColor("#1b5e20") if mem is False else QColor("#555")))

    def web_search(self, levels):
        d = self.current()
        if not d or not d["report"] or not d["report"].chosen_part:
            QMessageBox.information(self, "Нет партномера", "Сначала распознайте фото или введите партномер.")
            return
        r = d["report"]

        def work(progress, cancel):
            self.pipe.search_web(r, progress=progress, cancel=cancel, levels=levels)
            return r
        self.start_job(work, lambda _r: self._refresh_current())

    def reidentify(self):
        d = self.current()
        if not d:
            return
        text = self.marking.toPlainText().strip()
        if not text:
            return
        if d["report"] is None:
            path = self.current_path()

            def work(progress, cancel):
                return path, *self.pipe.analyze_image(path, progress=progress, marking_override=text)
            self.start_job(work, self._analyzed)
            return
        r = d["report"]

        def work2(progress, cancel):
            self.pipe.identify(r, text, [], progress)
            return r
        self.start_job(work2, lambda _r: self._refresh_current())

    def choose_part(self):
        d = self.current()
        part = self.part_box.currentText().strip().split("  (")[0]
        if not d or not part:
            return
        if d["report"] is None:
            self.marking.setPlainText(part)
            self.reidentify()
            return
        r = d["report"]

        def work(progress, cancel):
            self.pipe.set_part(r, part, progress)
            return r
        self.start_job(work, lambda _r: self._refresh_current())

    def apply_chip(self):
        d = self.current()
        if not d or not d["report"]:
            return
        r = d["report"]
        r.chip.package = self.pkg_box.currentText().strip()
        r.chip.pins = self.pins.value()
        r.chip.pins_estimated = False

        def work(progress, cancel):
            self.pipe.evaluate(r, progress)
            return r
        self.start_job(work, lambda _r: self._refresh_current())

    def _refresh_current(self):
        p = self.current_path()
        if p:
            self._mark_item(p)
        self.show_current()

    # ------------------------------------------------------------ показ
    def show_current(self, *_):
        d = self.current()
        self.variant_box.blockSignals(True)
        self.variant_box.clear()
        if not d:
            self.variant_box.blockSignals(False)
            return
        path = self.current_path()
        self.img_label.setPixmap(QPixmap(path).scaled(520, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.variant_box.addItem("Исходное фото")
        for v in d["variants"]:
            self.variant_box.addItem("Вариант: " + v.name)
        r = d["report"]
        if r and r.ocr and r.ocr.best_variant:
            best = r.ocr.best_variant.split("_rot")[0]
            idx = self.variant_box.findText("Вариант: " + best)
            if idx >= 0:
                self.variant_box.setCurrentIndex(idx)
                self.show_variant(idx)
        self.variant_box.blockSignals(False)

        self.part_box.clear()
        self.hits.setRowCount(0)
        if not r:
            self.marking.setPlainText("")
            self.report_view.setHtml("<p style='color:#777'>Фото ещё не распознано. Нажмите «Распознать».</p>")
            return
        self.marking.setPlainText(r.ocr.best_text if r.ocr else "")
        for i, c in enumerate(r.candidates):
            self.part_box.addItem("%s  (%d%%)" % (c.part, c.score * 100))
            self.part_box.setItemData(i, "%s\n%s" % (c.description, c.reason), Qt.ToolTipRole)
        idx = next((i for i, c in enumerate(r.candidates) if c.part == r.chosen_part), -1)
        if idx >= 0:
            self.part_box.setCurrentIndex(idx)
        else:
            self.part_box.setEditText(r.chosen_part)
        i = self.pkg_box.findText(r.chip.package)
        if i >= 0:
            self.pkg_box.setCurrentIndex(i)
        else:
            self.pkg_box.setEditText(r.chip.package)
        self.pins.setValue(r.chip.pins or 0)
        for box in (self.part_box, self.pkg_box):
            box.lineEdit().setCursorPosition(0)
        self.report_view.setHtml(self.pipe.render(r, d["variants"]))
        self._fill_hits(r)

    def show_variant(self, idx):
        d = self.current()
        if not d:
            return
        if idx <= 0:
            path = self.current_path()
            self.img_label.setPixmap(QPixmap(path).scaled(520, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif idx - 1 < len(d["variants"]):
            self.img_label.setPixmap(np_to_pixmap(d["variants"][idx - 1].image))

    def _fill_hits(self, r):
        self.hits.setRowCount(len(r.hits))
        for i, h in enumerate(r.hits):
            vals = [LEVEL_RU.get(h.level, h.level), h.source, h.title, "%d%%" % (h.score * 100),
                    "да" if h.is_pdf else "", h.location, h.note if h.allowed or h.is_local else "вне белого списка — только вручную"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if h.location == r.datasheet_path:
                    it.setBackground(QColor("#e3f2fd"))
                if not h.allowed and not h.is_local:
                    it.setForeground(QColor("#999"))
                self.hits.setItem(i, j, it)
        self.hits.resizeColumnToContents(0)
        self.hits.resizeColumnToContents(3)

    def _selected_hit(self):
        d = self.current()
        row = self.hits.currentRow()
        if not d or not d["report"] or row < 0 or row >= len(d["report"].hits):
            QMessageBox.information(self, "Выберите строку", "Выберите документ в таблице.")
            return None, None
        return d["report"], d["report"].hits[row]

    def use_hit(self):
        r, h = self._selected_hit()
        if not h:
            return
        if h.is_local:
            r.datasheet_path = h.location
            db = self.ctx.modules["local_db"]
            if hasattr(db, "confirm_part"):
                db.confirm_part(h.location, r.chosen_part)

            def work(progress, cancel):
                self.pipe.evaluate(r, progress)
                return r
            self.start_job(work, lambda _r: (self._refresh_current(), self.tabs.setCurrentIndex(0)))
        else:
            self.download_hit()

    def download_hit(self):
        r, h = self._selected_hit()
        if not h or h.is_local:
            return
        if not h.allowed:
            QMessageBox.information(self, "Сайт вне белого списка",
                                    "Программа не обращается к сайту %s.\n\nМожно открыть ссылку в браузере "
                                    "вручную, скачать PDF и добавить через «База → Добавить свой PDF»." % h.location)
            return

        def work(progress, cancel):
            ok = self.pipe.download_hit(r, h, progress)
            return ok
        self.start_job(work, lambda ok: self._after_download(r, h, ok))

    def _after_download(self, r, h, ok):
        self._refresh_current()
        if ok:
            self.tabs.setCurrentIndex(0)
            return
        if h.note.startswith("не подтверждён"):
            q = QMessageBox.question(self, "Партномер не найден в PDF",
                                     "В скачанном PDF нет «%s». Возможно, это datasheet на семейство или скан.\n\n"
                                     "Всё равно сохранить в базу и использовать?" % r.chosen_part)
            if q == QMessageBox.Yes:
                self.start_job(lambda progress, cancel: self.pipe.download_hit(r, h, progress, force_accept=True),
                               lambda _ok: (self._refresh_current(), self.tabs.setCurrentIndex(0)))

    def open_hit_pdf(self):
        r, h = self._selected_hit()
        if not h:
            return
        if not h.is_local:
            QMessageBox.information(self, "Сначала скачайте", "Откройте PDF после скачивания и проверки.")
            return
        self._open_path(h.location)

    def open_hit_browser(self):
        r, h = self._selected_hit()
        if not h:
            return
        if h.is_local:
            self._open_path(os.path.dirname(h.location))
            return
        if QMessageBox.question(self, "Открыть в браузере",
                                "Ссылка откроется в вашем браузере (вне защиты программы):\n\n%s" % h.location) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl(h.location))

    def copy_hit(self):
        r, h = self._selected_hit()
        if h:
            QApplication.clipboard().setText(h.location)
            self.log("Скопировано: " + h.location)

    # ------------------------------------------------------------ база и сеть
    def index_db(self):
        roots = self.ctx.config["paths"].get("scan_roots", [])
        if not roots:
            QMessageBox.information(self, "Нет папок", "Добавьте папки с datasheet в «Настройках». "
                                                       "Скачанные файлы будут проиндексированы в любом случае.")
        db = self.ctx.modules["local_db"]
        self.start_job(lambda progress, cancel: db.index(roots, progress, cancel),
                       lambda n: self.log("Проиндексировано файлов: %s" % n))

    def import_catalog(self):
        p = QFileDialog.getOpenFileName(self, "Каталог деталей: архив библиотек KiCad (.zip) или CSV", "",
                                        "KiCad zip / CSV (*.zip *.csv);;Все файлы (*.*)")[0]
        if not p:
            p = QFileDialog.getExistingDirectory(self, "…или папка с библиотеками KiCad (.kicad_sym/.dcm)")
        if not p:
            return
        db = self.ctx.modules["local_db"]
        self.start_job(lambda progress, cancel: db.import_catalog(p, progress),
                       lambda n: self.log("Каталог: импортировано %s деталей" % n))

    def add_own_pdf(self):
        d = self.current()
        if not d or not d["report"] or not d["report"].chosen_part:
            QMessageBox.information(self, "Нет партномера", "Выберите распознанное фото.")
            return
        p = QFileDialog.getOpenFileName(self, "PDF datasheet", "", "PDF (*.pdf)")[0]
        if not p:
            return
        r = d["report"]
        saved = self.ctx.modules["local_db"].add_to_library(p, r.chosen_part, {"source": "manual", "url": p})
        r.datasheet_path = saved
        self.start_job(lambda progress, cancel: (self.pipe.set_part(r, r.chosen_part, progress), r)[1],
                       lambda _r: self._refresh_current())

    def db_stats(self):
        st = self.ctx.modules["local_db"].stats()
        QMessageBox.information(self, "База", "Файлов в индексе: %(files)d\nСкачано программой: %(library)d\n"
                                              "Деталей в каталоге: %(catalog)d\n\nИндекс: %(db)s\nБиблиотека: %(library_dir)s" % st)

    def diagnose(self):
        ws = self.ctx.modules["web_search"]
        self.start_job(lambda progress, cancel: ws.diagnose(progress, cancel),
                       lambda rows: DiagnosticsDialog(rows, self).exec_())

    def settings(self):
        dlg = SettingsDialog(self.ctx, self)
        if dlg.exec_():
            self._load_context()
            self.log("Настройки сохранены и применены")

    def show_modules(self):
        lines = ["%-11s %s" % (k, v) for k, v in self.ctx.config["modules"].items()]
        QMessageBox.information(self, "Модули", "Модули программы (меняются в config.json → modules):\n\n" + "\n".join(lines))

    # ------------------------------------------------------------ отчёты
    def save_report(self):
        d = self.current()
        if not d or not d["report"]:
            return
        r = d["report"]
        rdir = resolve_path(self.app_dir, self.ctx.config["paths"]["reports_dir"])
        os.makedirs(rdir, exist_ok=True)
        name = "%s_%s.html" % (safe_filename(r.chosen_part or "chip"), datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        p = QFileDialog.getSaveFileName(self, "Сохранить отчёт", os.path.join(rdir, name), "HTML (*.html)")[0]
        if p:
            with io.open(p, "w", encoding="utf-8") as f:
                f.write(self.pipe.render(r, d["variants"]))
            self.log("Отчёт сохранён: " + p)

    def save_summary(self):
        rows = [(p, d["report"]) for p, d in self.items.items() if d["report"]]
        if not rows:
            return
        rdir = resolve_path(self.app_dir, self.ctx.config["paths"]["reports_dir"])
        os.makedirs(rdir, exist_ok=True)
        p = QFileDialog.getSaveFileName(self, "Сводка", os.path.join(rdir, "svodka_%s.csv" %
                                                                     datetime.datetime.now().strftime("%Y%m%d_%H%M")),
                                        "CSV (*.csv)")[0]
        if not p:
            return
        with io.open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Фото", "Маркировка", "Партномер", "Datasheet", "Сверка", "Оценка сверки",
                        "Память", "Типы памяти", "Итог по памяти"])
            for path, r in rows:
                mem = r.memory
                w.writerow([path, (r.ocr.best_text if r.ocr else "").replace("\n", " / "), r.chosen_part,
                            r.datasheet_path, r.comparison.verdict if r.comparison else "",
                            "%d%%" % (r.comparison.score * 100) if r.comparison else "",
                            {True: "ДА", False: "НЕТ", None: "?"}[mem.has_memory if mem else None],
                            "; ".join("%s %s" % (i.kind, i.size) for i in (mem.items if mem else [])),
                            mem.summary if mem else ""])
        self.log("Сводка сохранена: " + p)

    def _open_path(self, p):
        if p and os.path.exists(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        else:
            self.log("Не найдено: %s" % p)

    def closeEvent(self, e):
        self.stop_job()
        if self.job:
            self.job.wait(3000)
        if self.ctx:
            for mod in self.ctx.modules.values():
                mod.close()
        e.accept()


def main(app_dir: str) -> int:
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName("ChipFinder")
    # русские подписи стандартных кнопок Qt (Yes/No → Да/Нет)
    from PyQt5.QtCore import QLibraryInfo, QLocale, QTranslator
    tr = QTranslator(app)
    if tr.load(QLocale("ru_RU"), "qtbase", "_", QLibraryInfo.location(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    w = MainWindow(app_dir)
    w.show()
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:
        w.add_files(args)
    return app.exec_()
