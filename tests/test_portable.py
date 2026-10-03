# -*- coding: utf-8 -*-
"""Портативная сборка: поиск Tesseract в папке программы и состав архива (без сети)."""
import io
import os
import sys
import zipfile

import pytest

from chipfinder.modules.ocr_tesseract import find_tesseract

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
bp = pytest.importorskip("build_portable", reason="нет tools/ (портативная сборка)")


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


def test_find_tesseract_bundled(tmp_path):
    exe = _touch(str(tmp_path / "Папка с пробелом" / "tesseract" / "tesseract.exe"))
    app = os.path.dirname(os.path.dirname(exe))
    assert find_tesseract(app) == exe
    # путь из настроек важнее встроенного
    own = _touch(str(tmp_path / "own" / "tesseract.exe"))
    assert find_tesseract(app, own) == own
    # несуществующий путь из настроек — берём встроенный
    assert find_tesseract(app, str(tmp_path / "нет.exe")) == exe


def test_qt_plugins_dir_found():
    # путь к плагинам Qt задаётся явно: из папки с русскими буквами PyQt5 сам его не находит
    pytest.importorskip("PyQt5")
    from chipfinder.gui.main_window import qt_plugins_dir
    assert os.path.isdir(os.path.join(qt_plugins_dir(), "platforms"))


def test_requirements_for_windows():
    reqs = bp.windows_requirements(bp.read_lines(os.path.join(bp.ROOT, "requirements.txt")))
    assert "PyQt5-Qt5==5.15.2" in reqs
    assert not any("5.15.19" in r or ";" in r for r in reqs)
    assert all("==" in r for r in reqs if not r.startswith("certifi"))


def test_program_files_exclude_private():
    files = [rel for _src, rel in bp.program_files()]
    assert "run.py" in files and "config.default.json" in files
    assert any(f.startswith("chipfinder/") for f in files)
    assert any(f.startswith("tests/samples/") for f in files)
    bad = ("my_test/", "my_reports/", ".venv/", ".git/", "tools/", "config.json", "data/library/x")
    assert not any(f.startswith(b) for f in files for b in bad)
    assert not any("__pycache__" in f or f.endswith(".pyc") for f in files)


def test_pth_enables_site_packages():
    lines = bp.PTH.splitlines()
    assert "python38.zip" in lines and "Lib\\site-packages" in lines and "import site" in lines


def test_bat_files_cp866_crlf():
    for name, text in bp.BATS.items():
        data = bp.bat_bytes(text)
        assert b"\r\n" in data and b"\n\n" not in data.replace(b"\r\n", b"")
        data.decode("cp866")
    assert "ChipFinder.bat" in bp.BATS and "Самопроверка.bat" in bp.BATS
    assert "pythonw.exe" in bp.BATS["ChipFinder.bat"]
    assert "--selftest" in bp.BATS["Самопроверка.bat"]


def test_zip_russian_names_cp866(tmp_path):
    src = tmp_path / "src"
    _touch(str(src / "Самопроверка.bat"))
    _touch(str(src / "run.py"))
    out = str(tmp_path / "a.zip")
    bp.make_zip(str(src), out, "ChipFinder")
    with open(out, "rb") as f:
        raw = f.read()
    assert "ChipFinder/Самопроверка.bat".encode("cp866") in raw   # Проводник Win7 читает имена в cp866
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert {i.flag_bits & 0x800 for i in z.infolist()} == {0}   # без флага UTF-8
        assert "ChipFinder/run.py" in z.namelist()


def test_checks_in_build():
    files = [rel for _src, rel in bp.check_files()]
    for need in ("checks/run_checks.py", "checks/sysinfo.py", "checks/selftest.py", "checks/window.py",
                 "checks/ЧТО СДЕЛАТЬ.txt"):
        assert need in files
    assert "Проверка на работе.bat" in bp.BATS
    assert "checks\\run_checks.py" in bp.BATS["Проверка на работе.bat"]
