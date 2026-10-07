# -*- coding: utf-8 -*-
"""Фоновое выполнение задач, чтобы окно не зависало: пул потоков и объединение событий (§5)."""
from __future__ import annotations

import threading
import traceback
from typing import Optional

from PyQt5.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

from ..core.interfaces import CancelToken
from ..recognition.consent import NO, ONCE, SESSION


class Job(QObject):
    """Фоновая задача: `fn(*args, progress=…, cancel=…)` выполняется в потоке пула, итог приходит сигналом.

    `on_progress` — куда отдавать ход работы прямо из фонового потока (окно ставит сюда объединитель
    событий); без него каждая строка уходит сигналом `progress`.
    """
    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.cancel = CancelToken()
        self.on_progress = None
        self._running = False
        self._ended = threading.Event()
        self._ended.set()

    def isRunning(self) -> bool:
        return self._running

    def wait(self, msecs: Optional[int] = None) -> bool:
        """Ждёт конца задачи; False — не дождались за `msecs`."""
        return self._ended.wait(None if msecs is None else msecs / 1000.0)

    def begin(self) -> None:
        self._running = True
        self._ended.clear()

    def run(self) -> None:
        res, error = None, ""
        try:
            res = self.fn(*self.args, progress=self.on_progress or self.progress.emit, cancel=self.cancel,
                          **self.kwargs)
        except Exception as e:  # noqa
            error = "%s\n\n%s" % (e, traceback.format_exc())
        self._running = False       # до сигнала: обработчик результата вправе сразу запустить следующую задачу
        try:
            if error:
                self.failed.emit(error)
            else:
                self.done.emit(res)
            self.finished.emit()
        except RuntimeError:        # окно уже закрыто — сообщать некому
            pass
        finally:
            self._ended.set()


class _Runner(QRunnable):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job

    def run(self):
        self.job.run()


class TaskPool(QObject):
    """Пул потоков окна: распознавание, поиск, задачи расширений — всё, чему нельзя идти в потоке интерфейса."""

    def __init__(self, threads: int = 0, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads or max(4, min(8, QThread.idealThreadCount())))

    def start(self, job: Job) -> Job:
        job.begin()
        self._pool.start(_Runner(job))
        return job

    def wait(self, msecs: int = -1) -> bool:
        return self._pool.waitForDone(msecs)


class Coalescer(QObject):
    """Объединение событий: из любых потоков — `post`, в поток окна — пачкой, не чаще ~30 раз в секунду (§5).

    Пачка уходит сигналом `flushed` через `interval_ms` после первого события в ней; порядок сохраняется.
    """
    flushed = pyqtSignal(list)
    _wake = pyqtSignal()

    def __init__(self, interval_ms: int = 33, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._items = []
        self._waiting = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.flush)
        self._wake.connect(self._arm)             # из фонового потока сигнал приходит через очередь окна

    def post(self, item) -> None:
        with self._lock:
            self._items.append(item)
            first, self._waiting = not self._waiting, True
        if first:                                 # один сигнал на пачку, а не на событие
            try:
                self._wake.emit()
            except RuntimeError:                  # окно уже закрыто
                pass

    def _arm(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Отдать накопленное сейчас (только из потока окна)."""
        self._timer.stop()
        with self._lock:
            items, self._items = self._items, []
            self._waiting = False
        if items:
            self.flushed.emit(items)


class ConsentBridge(QObject):
    """Вопрос «Отправить фото в <сервис>?»: распознавание в фоновом потоке ждёт ответа из потока окна."""
    requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requested.connect(self._answer)      # из фонового потока сигнал приходит через очередь окна

    def ask(self, provider_id: str, title: str) -> str:
        if QThread.currentThread() is self.thread():
            return self.dialog(title)
        request = {"title": title, "answer": NO, "done": threading.Event()}
        self.requested.emit(request)
        request["done"].wait()
        return request["answer"]

    def _answer(self, request) -> None:
        try:
            request["answer"] = self.dialog(request["title"])
        finally:
            request["done"].set()

    def dialog(self, title: str) -> str:
        box = QMessageBox(self.parent())
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Отправка фото")
        box.setText("Отправить фото в «%s»?" % title)
        box.setInformativeText("Способы, работающие на этом компьютере, не дали уверенного результата. "
                               "Фото чипа уйдёт на внешний сервис. Без согласия ничего не отправляется.")
        once = box.addButton("Отправить это фото", QMessageBox.AcceptRole)
        session = box.addButton("Отправлять до закрытия программы", QMessageBox.YesRole)
        no = box.addButton("Не отправлять", QMessageBox.RejectRole)
        box.setDefaultButton(no)
        box.setEscapeButton(no)
        box.exec_()
        return {once: ONCE, session: SESSION}.get(box.clickedButton(), NO)
