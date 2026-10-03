# -*- coding: utf-8 -*-
"""Проверочный набор для Win7 (tools/win7_pack): запуск проверок, изоляция ошибок, состав отчёта."""
import json
import os
import subprocess
import sys
import zipfile

import pytest

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "win7_pack")
sys.path.insert(0, PACK)
rc = pytest.importorskip("run_checks", reason="нет tools/win7_pack")


def _checks(tmp_path):
    d = tmp_path / "checks"
    d.mkdir()
    (d / "good.py").write_text("def run(ctx):\n    ctx.todo('сделать А')\n    return {'x': 1}\n", encoding="utf-8")
    (d / "bad.py").write_text("def run(ctx):\n    raise RuntimeError('сломано')\n", encoding="utf-8")
    (d / "exits.py").write_text("import sys\ndef run(ctx):\n    sys.exit(3)\n", encoding="utf-8")
    (d / "helper.py").write_text("X = 1\n", encoding="utf-8")        # без run — не проверка
    (d / "_private.py").write_text("def run(ctx):\n    return {}\n", encoding="utf-8")
    (d / "ЧТО СДЕЛАТЬ.txt").write_text("Статичный список\n", encoding="utf-8")
    return str(d)


def test_errors_are_isolated(tmp_path):
    checks = _checks(tmp_path)
    app = tmp_path / "Моя программа"
    app.mkdir()
    results, todo = rc.run_all(str(app), checks, str(tmp_path / "out"), log=lambda *_: None)
    by = {r["name"]: r for r in results}
    assert set(by) == {"bad", "exits", "good"}
    assert by["good"]["status"] == "ok" and by["good"]["x"] == 1
    assert by["bad"]["status"] == "error" and "сломано" in by["bad"]["traceback"]
    assert by["exits"]["status"] == "error"
    assert todo == ["сделать А"]


def test_report_zip(tmp_path):
    checks = _checks(tmp_path)
    app = tmp_path / "app"
    (app / "logs").mkdir(parents=True)
    (app / "logs" / "x.log").write_text("лог", encoding="utf-8")
    out = str(tmp_path / "out")
    results, todo = rc.run_all(str(app), checks, out, log=lambda *_: None)
    path = rc.build_report(str(app), out, results, todo, checks)
    assert os.path.basename(path).startswith("отчёт_") and path.endswith(".zip")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert {"results.json", "отчёт.md", "ЧТО СДЕЛАТЬ.txt", "logs/x.log"} <= set(names)
        assert len(json.loads(z.read("results.json").decode("utf-8"))) == 3
        todo_text = z.read("ЧТО СДЕЛАТЬ.txt").decode("utf-8")
        assert "Статичный список" in todo_text and "сделать А" in todo_text
    # второй запуск в тот же день не затирает первый
    assert rc.build_report(str(app), out, results, todo, checks) != path


def test_only_and_order(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    for n in ("zzz", "window", "sysinfo", "aaa"):
        (d / (n + ".py")).write_text("def run(ctx):\n    return {}\n", encoding="utf-8")
    assert rc.discover(str(d)) == ["sysinfo", "window", "aaa", "zzz"]
    results, _ = rc.run_all(str(tmp_path), str(d), str(tmp_path / "o"), only=["aaa"], log=lambda *_: None)
    assert [r["name"] for r in results] == ["aaa"]


def test_default_app_dir():
    assert rc.default_app_dir("/x/ChipFinder/checks") == "/x/ChipFinder"
    assert rc.default_app_dir("/repo/tools/win7_pack") == "/repo"


def test_sysinfo_survives_everything(tmp_path):
    import sysinfo
    ctx = rc.Context(str(tmp_path), str(tmp_path))
    res = sysinfo.run(ctx)
    assert res["status"] == "ok"
    for key in ("windows", "codepages", "screen", "proxy", "disk", "dlls"):
        assert key in res
    assert res["disk"]["free_mb"] > 0


def test_selftest_check(tmp_path):
    import selftest
    app = tmp_path / "Папка с пробелом"
    app.mkdir()
    (app / "run.py").write_text("import sys\nprint('1 passed')\nsys.exit(0)\n", encoding="utf-8")
    ctx = rc.Context(str(app), str(tmp_path))
    res = selftest.run(ctx)
    assert res["status"] == "ok" and res["tail"] == ["1 passed"]
    (app / "run.py").write_text("import sys\nprint('1 failed')\nsys.exit(1)\n", encoding="utf-8")
    assert selftest.run(ctx)["status"] == "fail"


def test_window_check_snapshot(tmp_path):
    pytest.importorskip("PyQt5")
    root = os.path.dirname(PACK.rstrip(os.sep))
    root = os.path.dirname(root)
    png = tmp_path / "Снимок.png"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, os.path.join(PACK, "window.py"), "--snap", str(png), root], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    assert p.returncode == 0, p.stdout.decode("utf-8", "replace")
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
