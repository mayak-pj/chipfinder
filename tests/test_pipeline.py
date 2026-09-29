# -*- coding: utf-8 -*-
"""Конвейер на синтетических фото, без интернета."""
import os

import pytest

from chipfinder.core.pipeline import ChipPipeline

EXPECT = {
    "at24c02.png": ("24C02", True),
    "stm32.png": ("STM32F103", True),
    "w25q64_rot.png": ("W25Q64", True),
    "lm358_180.png": ("LM358", False),
}

pytestmark = pytest.mark.needs_tesseract


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_sample(ctx, samples_dir, tmp_path, name):
    want, mem = EXPECT[name]
    if not ctx.modules["ocr"].is_available():
        pytest.skip("OCR недоступен: " + ctx.modules["ocr"].error)
    ctx.modules["local_db"].index([os.path.join(samples_dir, "network_share")])
    pipe = ChipPipeline(ctx)
    r, variants = pipe.analyze_image(os.path.join(samples_dir, name))
    assert want in r.chosen_part
    assert r.memory is not None and r.memory.has_memory == mem
    html = pipe.render(r, variants)
    (tmp_path / (name + ".html")).write_text(html, encoding="utf-8")
