# -*- coding: utf-8 -*-
"""Снимки окна программы в PNG (ARCHITECTURE §5) — без экрана, для просмотра после шага, меняющего интерфейс.

    python tools/screenshots.py [--out my_reports/screens] [--prefix 7_2] [--themes light,dark] [--no-ocr]

Программа запускается во временной папке (настройки по умолчанию, пустая база): рабочие данные не трогаются.
На каждую тему: главное окно с образцами фото (первое распознано), вкладка «Документы», «Настройки», «Расширения».
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SAMPLES = ("stm32.png", "at24c02.png", "w25q64_rot.png", "lm358_180.png")


def make_app_dir(theme: str) -> str:
    """Временная папка программы (с русскими буквами в пути) с нужной темой в config.json."""
    app_dir = os.path.join(tempfile.mkdtemp(prefix="chipfinder_shots_"), u"программа")
    os.makedirs(os.path.join(app_dir, "data"))
    shutil.copy(os.path.join(ROOT, "config.default.json"), app_dir)
    shutil.copytree(os.path.join(ROOT, "data", "i18n"), os.path.join(app_dir, "data", "i18n"))
    for fn in os.listdir(os.path.join(ROOT, "data")):
        if fn.endswith(".json"):
            shutil.copy(os.path.join(ROOT, "data", fn), os.path.join(app_dir, "data"))
    with open(os.path.join(app_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"ui": {"theme": theme}, "network": {"offline": True}}, f)
    return app_dir


def sample_photos():
    d = os.path.join(ROOT, "tests", "samples")
    if not os.path.isdir(d):
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        import make_samples
        make_samples.main()
    return [os.path.join(d, n) for n in SAMPLES if os.path.isfile(os.path.join(d, n))]


def save(widget, path: str) -> None:
    from PyQt5.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    widget.grab().save(buf, "PNG")
    with open(path, "wb") as f:               # путь с русскими буквами: пишем сами, не через Qt
        f.write(bytes(buf.data()))
    print(path)


def shoot(app, theme: str, out_dir: str, prefix: str, ocr: bool) -> None:
    from chipfinder.gui.dialogs import ExtensionsDialog, SettingsDialog
    from chipfinder.gui.main_window import MainWindow
    app_dir = make_app_dir(theme)
    w = MainWindow(app_dir)
    try:
        w.show()
        photos = sample_photos()
        w.add_files(photos)
        if photos:
            w.list.setCurrentRow(0)
        if ocr and photos and w.ctx.modules["ocr"].is_available():
            r, variants = w.pipe.analyze_image(photos[0])       # здесь можно в потоке окна: экрана нет
            w._analyzed((photos[0], r, variants))
        app.processEvents()

        def name(part):
            return os.path.join(out_dir, "%s_%s_%s.png" % (prefix, theme, part))
        save(w, name("window"))
        w.tabs.setCurrentIndex(1)
        app.processEvents()
        save(w, name("documents"))
        w.tabs.setCurrentIndex(0)
        for part, dlg in (("settings", SettingsDialog(w.ctx, w)), ("extensions", ExtensionsDialog(w.ext, w))):
            dlg.show()
            app.processEvents()
            save(dlg, name(part))
            dlg.close()
    finally:
        w.close()
        shutil.rmtree(os.path.dirname(app_dir), ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=u"Снимки окна ChipFinder")
    ap.add_argument("--out", default=os.path.join(ROOT, "my_reports", "screens"))
    ap.add_argument("--prefix", default="window")
    ap.add_argument("--themes", default="light")
    ap.add_argument("--no-ocr", action="store_true", help=u"не распознавать образец (быстрее)")
    args = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from chipfinder.gui.main_window import install_russian, qt_plugins_dir
    plugins = qt_plugins_dir()
    if plugins:
        QCoreApplication.addLibraryPath(plugins)
    app = QApplication(sys.argv[:1])
    install_russian(app)
    for box in ("warning", "critical", "information"):      # без экрана на вопрос ответить некому
        setattr(QMessageBox, box, staticmethod(lambda *a, **k: 0))
    os.makedirs(args.out, exist_ok=True)
    for theme in [t.strip() for t in args.themes.split(",") if t.strip()]:
        shoot(app, theme, args.out, args.prefix, not args.no_ocr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
