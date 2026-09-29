# -*- coding: utf-8 -*-
"""Запуск ChipFinder: python run.py [фото ...]"""
import os
import sys


def app_dir():
    if getattr(sys, "frozen", False):          # собранный .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main():
    d = app_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    os.chdir(d)
    if sys.version_info < (3, 8):
        print("Нужен Python 3.8 или новее")
        return 1
    if "--selftest" in sys.argv:
        sys.path.insert(0, os.path.join(d, "tests"))
        import test_pipeline
        import test_web_offline
        return test_pipeline.main() or test_web_offline.main()
    from chipfinder.gui.main_window import main as gui_main
    return gui_main(d)


if __name__ == "__main__":
    sys.exit(main())
