# -*- coding: utf-8 -*-
"""Окно программы для тестов интерфейса: временная папка программы, без экрана, без сети."""
import os
import shutil
import sys

import pytest

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def qapp():
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    app_dir = tmp_path / u"программа"
    os.makedirs(str(app_dir / "data"))
    shutil.copy(os.path.join(APP, "config.default.json"), str(app_dir))
    shutil.copytree(os.path.join(APP, "data", "i18n"), str(app_dir / "data" / "i18n"))
    shutil.copy(os.path.join(APP, "data", "sources.json"), str(app_dir / "data"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    from digger.gui.main_window import MainWindow
    w = MainWindow(str(app_dir))
    yield w, qapp
    w.close()
