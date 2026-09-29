# -*- coding: utf-8 -*-
"""Фоновое выполнение задач, чтобы окно не зависало."""
from __future__ import annotations

import traceback

from PyQt5.QtCore import QThread, pyqtSignal

from ..core.interfaces import CancelToken


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
