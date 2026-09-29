# -*- coding: utf-8 -*-
"""Самопроверка без интернета: python tests/test_pipeline.py"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

from chipfinder.core.config import load_config, setup_logging  # noqa: E402
from chipfinder.core.interfaces import Context  # noqa: E402
from chipfinder.core.pipeline import ChipPipeline  # noqa: E402
from chipfinder.core.registry import load_modules  # noqa: E402

EXPECT = {
    "at24c02.png": ("24C02", True),
    "stm32.png": ("STM32F103", True),
    "w25q64_rot.png": ("W25Q64", True),
    "lm358_180.png": ("LM358", False),
}


def main():
    samples = os.path.join(HERE, "samples")
    if not os.path.isdir(samples):
        import make_samples
        make_samples.main()
    work = tempfile.mkdtemp(prefix="chipfinder_test_")
    cfg = load_config(APP)
    cfg["paths"].update({"db": os.path.join(work, "t.sqlite"), "library_dir": os.path.join(work, "lib"),
                         "quarantine_dir": os.path.join(work, "q"), "log_dir": os.path.join(work, "logs")})
    cfg["network"]["offline"] = True
    ctx = Context(cfg, APP, setup_logging(APP, cfg))
    load_modules(ctx)
    if not ctx.modules["ocr"].is_available():
        print("ПРОПУСК распознавания: " + ctx.modules["ocr"].error)
        print("Установите Tesseract (см. README.md) и повторите: venv\\Scripts\\python run.py --selftest")
        return 1
    n = ctx.modules["local_db"].index([os.path.join(samples, "network_share")])
    print("Индекс:", n, "файлов")
    pipe = ChipPipeline(ctx)
    fails = 0
    for name, (want, mem) in EXPECT.items():
        r, variants = pipe.analyze_image(os.path.join(samples, name))
        got = r.chosen_part
        ok_part = want in got
        ok_mem = (r.memory is not None and r.memory.has_memory == mem)
        print("\n=== %s" % name)
        print("  OCR      :", (r.ocr.best_text if r.ocr else "").replace("\n", " / "), "|", r.ocr.best_variant if r.ocr else "")
        print("  кандидаты:", ", ".join("%s %.2f" % (c.part, c.score) for c in r.candidates[:5]))
        print("  выбран   :", got, "OK" if ok_part else "ОШИБКА (ожидали %s)" % want)
        print("  корпус   :", r.chip.package, r.chip.pins, "ratio", r.chip.body_ratio)
        print("  datasheet:", os.path.basename(r.datasheet_path))
        if r.comparison:
            print("  сверка   :", r.comparison.verdict, r.comparison.score)
            for c in r.comparison.checks:
                print("     [%s] %s: %s" % (c.status, c.name, c.detail))
        print("  память   :", r.memory.summary if r.memory else None, "OK" if ok_mem else "ОШИБКА")
        html = pipe.render(r, variants)
        with open(os.path.join(work, name + ".html"), "w", encoding="utf-8") as f:
            f.write(html)
        fails += (not ok_part) + (not ok_mem)
    print("\nОтчёты:", work)
    print("ИТОГ:", "всё верно" if fails == 0 else "%d ошибок" % fails)
    for m in ctx.modules.values():
        m.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
