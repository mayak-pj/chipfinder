# -*- coding: utf-8 -*-
"""Портативная сборка для Windows 7 x64 (без установки и прав администратора).

    python tools/build_portable.py [--out dist] [--no-tesseract] [--no-zip]

Состав папки ChipFinder/: python/ (embeddable 3.8.10 + пакеты в Lib/site-packages), tesseract/ (распакованный
установщик UB Mannheim, tessdata/eng), программа, ChipFinder.bat, Самопроверка.bat. Пакеты — колёса win_amd64
для cp38 (pip --platform), поэтому сборку можно запустить и на Mac, чтобы проверить состав. Tesseract
распаковывается через 7-Zip (на CI есть; на Mac без 7-Zip — ключ --no-tesseract).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from packaging.requirements import Requirement

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "ChipFinder"
ZIP_NAME = "ChipFinder_portable_win7_x64.zip"

PY_URL = "https://www.python.org/ftp/python/3.8.10/python-3.8.10-embed-amd64.zip"
PY_SHA256 = "abbe314e9b41603dde0a823b76f5bbbe17b3de3e5ac4ef06b759da5466711271"
# 5.3.0 собран до того, как MSYS2 перестал поддерживать Win7 (2023); более новые сборки на Win7 не проверены
TESS_VERSION = "5.3.0.20221214"
TESS_URL = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-v%s.exe" % TESS_VERSION
TESS_SHA256 = "175a326853f87474132c284072a821dd819c63b707b01879b309f80bd6c0ab1e"

# pytest для «Самопроверки» (с зависимостями — ставится с --no-deps, поэтому перечислено всё)
SELFTEST_REQS = ["pytest==8.3.5", "pluggy==1.5.0", "iniconfig==2.0.0", "exceptiongroup==1.2.2",
                 "tomli==2.0.2", "colorama==0.4.6"]

# окружение цели для маркеров requirements.txt
WIN_ENV = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64",
           "python_version": "3.8", "python_full_version": "3.8.10", "implementation_name": "cpython",
           "platform_python_implementation": "CPython"}

INCLUDE = ["run.py", "config.default.json", "README.md", "requirements.txt", "pyproject.toml",
           "chipfinder", "plugins", "data", "tests"]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = (".pyc", ".sqlite", ".sqlite-journal")
SKIP_PREFIX = ("data/quarantine/",)

# ._pth: stdlib из zip, пакеты из Lib\site-packages; наличие файла включает изолированный режим
# (реестр и переменные PYTHON* игнорируются)
PTH = "python38.zip\n.\nLib\\site-packages\nimport site\n"

_ENV = ('if not exist tmp mkdir tmp\n'
        'if not exist logs mkdir logs\n'
        'set "TEMP=%~dp0tmp"\n'
        'set "TMP=%~dp0tmp"\n')
BATS = {
    "ChipFinder.bat": (
        '@echo off\n'
        'rem Запуск ChipFinder (портативная версия, без установки)\n'
        'cd /d "%~dp0"\n' + _ENV +
        'start "" "%~dp0python\\pythonw.exe" "%~dp0run.py" %*\n'),
    "Самопроверка.bat": (
        '@echo off\n'
        'rem Самопроверка ChipFinder: тесты программы без интернета\n'
        'cd /d "%~dp0"\n' + _ENV +
        'echo Идёт самопроверка, подождите 1-3 минуты...\n'
        '"%~dp0python\\python.exe" "%~dp0run.py" --selftest > "%~dp0logs\\selftest.txt" 2>&1\n'
        'set RC=%ERRORLEVEL%\n'
        'type "%~dp0logs\\selftest.txt"\n'
        'echo.\n'
        'if "%RC%"=="0" (echo САМОПРОВЕРКА ПРОЙДЕНА) else (echo САМОПРОВЕРКА НЕ ПРОЙДЕНА, код %RC%)\n'
        'echo Отчёт: logs\\selftest.txt\n'
        'pause\n'),
}


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return [s.strip() for s in f if s.strip() and not s.strip().startswith("#")]


def windows_requirements(lines):
    """Строки requirements.txt, применимые к Win7 x64 / Python 3.8.10, без маркеров."""
    out = []
    for line in lines:
        r = Requirement(line)
        if r.marker is None or r.marker.evaluate(WIN_ENV):
            out.append(r.name + str(r.specifier))
    return out


def program_files():
    """[(путь_на_диске, путь_в_сборке)] — только перечисленное в INCLUDE, без кэшей и данных пользователя."""
    out = []
    for item in INCLUDE:
        src = os.path.join(ROOT, item)
        if os.path.isfile(src):
            out.append((src, item))
            continue
        for d, dirs, files in os.walk(src):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for fn in sorted(files):
                p = os.path.join(d, fn)
                rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
                if fn.endswith(SKIP_EXT) or rel.startswith(SKIP_PREFIX) or fn == ".DS_Store":
                    continue
                if rel.startswith("data/library/") and fn != ".keep":
                    continue
                out.append((p, rel))
    return out


def bat_bytes(text):
    """cmd.exe читает .bat в OEM-кодировке (cp866 на русской Windows), строки — CRLF."""
    return text.replace("\n", "\r\n").encode("cp866")


class _OemZipInfo(zipfile.ZipInfo):
    """Русские имена в cp866 без флага UTF-8: встроенный распаковщик Win7 флаг UTF-8 не понимает."""
    def _encodeFilenameFlags(self):
        try:
            return self.filename.encode("ascii"), self.flag_bits
        except UnicodeEncodeError:
            return self.filename.encode("cp866"), self.flag_bits


def make_zip(src_dir, out_path, arc_root):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for d, dirs, files in os.walk(src_dir):
            dirs.sort()
            for fn in sorted(files):
                p = os.path.join(d, fn)
                arc = arc_root + "/" + os.path.relpath(p, src_dir).replace(os.sep, "/")
                zi = _OemZipInfo.from_file(p, arc)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as f, z.open(zi, "w") as w:
                    shutil.copyfileobj(f, w, 1 << 20)


def fetch(url, sha256, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, url.rsplit("/", 1)[1])
    if not os.path.isfile(path):
        print("Скачиваю", url)
        with urllib.request.urlopen(url, timeout=120) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(path + ".part", path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != sha256:
        os.remove(path)
        raise SystemExit("Неверная контрольная сумма %s: %s" % (url, h.hexdigest()))
    return path


def install_packages(site_dir, reqs):
    """Колёса для Windows x64 / cp38 независимо от машины сборки. pip (CI) или uv (окружение на Mac без pip)."""
    try:
        import pip  # noqa: F401
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", "--no-compile",
               "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.8", "--implementation", "cp",
               "--abi", "cp38", "--target", site_dir] + reqs
    except ImportError:
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit("Нужен pip или uv")
        cmd = [uv, "pip", "install", "--no-deps", "--only-binary", ":all:", "--python-platform", "x86_64-pc-windows-msvc",
               "--python-version", "3.8", "--target", site_dir] + reqs
    subprocess.check_call(cmd)


def extract_tesseract(installer, dest):
    seven = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if not seven:
        raise SystemExit("Нужен 7-Zip (7z/7zz) для распаковки Tesseract, или ключ --no-tesseract")
    subprocess.check_call([seven, "x", "-y", "-bd", "-o" + dest, installer], stdout=subprocess.DEVNULL)
    for junk in ("$PLUGINSDIR", "$TEMP", "uninstall.exe"):
        p = os.path.join(dest, junk)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.isfile(p):
            os.remove(p)
    for need in ("tesseract.exe", os.path.join("tessdata", "eng.traineddata"), os.path.join("tessdata", "configs", "tsv")):
        if not os.path.isfile(os.path.join(dest, need)):
            raise SystemExit("В распакованном Tesseract нет " + need)


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:  # noqa
        return "?"


def build(out_dir, with_tesseract=True, with_zip=True):
    cache = os.path.join(out_dir, "cache")
    app = os.path.join(out_dir, NAME)
    if os.path.isdir(app):
        shutil.rmtree(app)
    os.makedirs(app)

    py = os.path.join(app, "python")
    with zipfile.ZipFile(fetch(PY_URL, PY_SHA256, cache)) as z:
        z.extractall(py)
    with open(os.path.join(py, "python38._pth"), "w", newline="\r\n") as f:
        f.write(PTH)
    reqs = windows_requirements(read_lines(os.path.join(ROOT, "requirements.txt"))) + SELFTEST_REQS
    install_packages(os.path.join(py, "Lib", "site-packages"), reqs)

    if with_tesseract:
        extract_tesseract(fetch(TESS_URL, TESS_SHA256, cache), os.path.join(app, "tesseract"))

    for src, rel in program_files():
        dst = os.path.join(app, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    for name, text in BATS.items():
        with open(os.path.join(app, name), "wb") as f:
            f.write(bat_bytes(text))
    with open(os.path.join(app, "build_info.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("commit: %s\npython: 3.8.10 embeddable amd64\ntesseract: %s\npackages:\n  %s\n"
                % (git_commit(), TESS_VERSION if with_tesseract else "нет", "\n  ".join(reqs)))

    if not with_zip:
        return app
    out = os.path.join(out_dir, ZIP_NAME)
    make_zip(app, out, NAME)
    print("Готово: %s (%.1f МБ)" % (out, os.path.getsize(out) / 1e6))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--no-tesseract", action="store_true", help="без Tesseract (проверка состава на Mac)")
    ap.add_argument("--no-zip", action="store_true", help="только папка, без архива")
    a = ap.parse_args()
    build(os.path.abspath(a.out), not a.no_tesseract, not a.no_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
