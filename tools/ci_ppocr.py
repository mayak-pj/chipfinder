# -*- coding: utf-8 -*-
"""Шаг CI: проверка `ppocr` из проверочного набора, запущенная встроенным Python собранной папки.

    dist\\ChipFinder\\python\\python.exe tools/ci_ppocr.py dist\\ChipFinder

Кладёт в `фото/` образцы tests/samples (ответы — в ответы.csv), запускает checks/run_checks.py --only ppocr_check
и требует: PP-OCR загрузился и прочитал не меньше MIN_READ образцов.
"""
import csv
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# что напечатано на образцах (tests/make_samples.py): одна строка маркировки
ANSWERS = {"at24c02.png": "24C02N", "stm32.png": "STM32F103", "w25q64_rot.png": "W25Q64JVSIQ", "lm358_180.png": "LM358"}
MIN_READ = 3


def main(app):
    app = os.path.abspath(app)
    photos = os.path.join(app, "фото")
    os.makedirs(photos, exist_ok=True)
    for fn in ANSWERS:
        shutil.copy2(os.path.join(ROOT, "tests", "samples", fn), os.path.join(photos, fn))
    with open(os.path.join(photos, "ответы.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["файл", "ответ"])
        w.writerows(sorted(ANSWERS.items()))
    rc = subprocess.call([sys.executable, os.path.join(app, "checks", "run_checks.py"), "--app-dir", app, "--only", "ppocr_check"])
    with open(os.path.join(app, "tmp", "report", "results.json"), encoding="utf-8") as f:
        res = json.load(f)[0]
    print(json.dumps({k: v for k, v in res.items() if k != "traceback"}, ensure_ascii=False, indent=2))
    if res.get("traceback"):
        print(res["traceback"])
    if rc != 0 or res.get("status") != "ok":
        print("ppocr: проверка не пройдена")
        return 1
    auto = res["stats"]["ppocr_main"]["auto"]
    if auto < MIN_READ:
        print("ppocr: прочитано %d из %d, нужно не меньше %d" % (auto, len(ANSWERS), MIN_READ))
        return 1
    print("ppocr: прочитано %d из %d" % (auto, len(ANSWERS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "ChipFinder")))
