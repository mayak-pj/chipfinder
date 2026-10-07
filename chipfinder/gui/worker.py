# -*- coding: utf-8 -*-
"""Фоновое выполнение задач, чтобы окно не зависало."""
from __future__ import annotations

import threading
import traceback

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

from ..core.interfaces import CancelToken
from ..recognition.consent import NO, ONCE, SESSION


class Job(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.cancel = CancelToken()

    def run(self):
        try:
            res = self.fn(*self.args, progress=self.progress.emit, cancel=self.cancel, **self.kwargs)
            self.done.emit(res)
        except Exception as e:  # noqa
            self.failed.emit("%s\n\n%s" % (e, traceback.format_exc()))


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
