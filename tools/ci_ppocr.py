# -*- coding: utf-8 -*-
"""Шаг CI: проверки `ppocr_check` и `program_ocr` набора, запущенные встроенным Python собранной папки.

    dist\\Digger\\python\\python.exe tools/ci_ppocr.py dist\\Digger

Кладёт в `фото/` образцы tests/samples (ответы — в ответы.csv), запускает checks/run_checks.py --only ppocr_check program_ocr
и требует: PP-OCR загрузился и прочитал не меньше MIN_READ образцов, а программа (менеджер распознавания)
прочитала не меньше MIN_READ образцов провайдером ppocr — с библиотеками из site-packages, не из checks/.
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
    rc = subprocess.call([sys.executable, os.path.join(app, "checks", "run_checks.py"), "--app-dir", app,
                          "--only", "ppocr_check", "program_ocr"])
    with open(os.path.join(app, "tmp", "report", "results.json"), encoding="utf-8") as f:
        results = {r["name"]: r for r in json.load(f)}
    for res in results.values():
        print(json.dumps({k: v for k, v in res.items() if k != "traceback"}, ensure_ascii=False, indent=2))
        if res.get("traceback"):
            print(res["traceback"])
    bad = [n for n in ("ppocr_check", "program_ocr") if results.get(n, {}).get("status") != "ok"]
    if rc != 0 or bad:
        print("ppocr: проверка не пройдена: " + ", ".join(bad))
        return 1
    auto = results["ppocr_check"]["stats"]["ppocr_main"]["auto"]
    prog = results["program_ocr"]
    print("ppocr_check: прочитано %d из %d; program_ocr: %d из %d (%s)"
          % (auto, len(ANSWERS), prog["read"], len(ANSWERS), ", ".join(prog["providers"])))
    if auto < MIN_READ or prog["read"] < MIN_READ or "ppocr" not in prog["providers"]:
        print("ppocr: нужно не меньше %d прочитанных образцов, и программа должна читать провайдером ppocr" % MIN_READ)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "Digger")))
