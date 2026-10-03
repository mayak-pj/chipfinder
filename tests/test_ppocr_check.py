# -*- coding: utf-8 -*-
"""tools/win7_pack/ppocr_check.py: правильный ответ — имя файла, таблица точности, запуск как проверка набора."""
import os
import sys

import cv2
import numpy as np
import pytest

PACK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "win7_pack")
sys.path.insert(0, PACK)
pc = pytest.importorskip("ppocr_check", reason="нет tools/win7_pack/ppocr_check.py")


def _png(path, text="X"):
    img = np.full((40, 120, 3), 255, np.uint8)
    cv2.putText(img, text, (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".png", img)
    buf.tofile(str(path))


def test_norm_and_expected_name():
    assert pc.norm("ST-M32 f4/37") == "STM32F437"
    assert pc.expected_from_name("ATEN SICH-1000.JPG") == "ATENSICH1000"
    assert pc.expected_from_name("NT5AD1024M8C3 рентген.JPG") == "NT5AD1024M8C3"   # русские слова — пометки
    assert pc.expected_from_name("k9ckgy8j5c.JPG") == "K9CKGY8J5C"


def test_is_match():
    assert pc.is_match("25q64 fv", "25Q64FV")
    assert not pc.is_match("25Q64", "25Q64FV")
    assert not pc.is_match("", "")


def test_collect_recursive_with_answers_csv(tmp_path):
    sub = tmp_path / "Вырезанные" / "вложенная"
    sub.mkdir(parents=True)
    _png(tmp_path / "Вырезанные" / "ABC123 пометка.JPG")
    _png(sub / "w25q64.png")
    _png(sub / "other.png")
    (sub / "notes.txt").write_text("не картинка", encoding="utf-8")
    (tmp_path / "ответы.csv").write_text("файл;ответ\nother.png;ZZ-9\n", encoding="utf-8-sig")
    items = pc.collect(str(tmp_path))
    by = {os.path.basename(p): e for p, e in items}
    assert by == {"ABC123 пометка.JPG": "ABC123", "w25q64.png": "W25Q64", "other.png": "ZZ9"}


def test_evaluate_counts_rotations(tmp_path):
    _png(tmp_path / "AAA111.png")
    _png(tmp_path / "BBB222.png")
    items = pc.collect(str(tmp_path))
    calls = []

    def engine(img):
        calls.append(img.shape)
        # «читает» только первую картинку и только в исходном положении (широкая картинка)
        return [("aaa 111", 0.9)] if img.shape[1] > img.shape[0] and len(calls) <= 4 else [("мусор", 0.1)]

    stats, rows = pc.evaluate(items, {"v": engine}, rotations=(0, 90))
    s = stats["v"]
    assert s["total"] == 2
    assert s["rot0"] == 1 and s["any"] == 1
    assert len(rows) == 2 and rows[0]["file"].endswith(".png")
    assert any(r["variants"]["v"]["ok"] for r in rows)


def test_report_markdown_has_table(tmp_path):
    stats = {"main": {"total": 4, "rot0": 3, "any": 4, "auto": 3, "seconds": 0.5, "errors": 0}}
    rows = [{"file": "A.jpg", "expected": "A1", "variants": {"main": {"ok": False, "lines": ["x"], "rot": 0}}}]
    md = pc.report_md(stats, rows, "тест")
    assert "| main |" in md and "75" in md and "A.jpg" in md


def test_run_creates_photos_dir_and_asks_user(tmp_path):
    class Ctx(object):
        app_dir = str(tmp_path)
        photos_dir = str(tmp_path / "фото")
        work_dir = str(tmp_path / "w")
        todo_items = []

        def todo(self, t):
            self.todo_items.append(t)

    os.makedirs(Ctx.work_dir)
    c = Ctx()
    res = pc.run(c)
    assert os.path.isdir(c.photos_dir)
    assert res["status"] == "skip" and c.todo_items
