# -*- coding: utf-8 -*-
"""Синтетические фото чипов и тестовый datasheet для самопроверки.
Запуск: python tests/make_samples.py  → папка tests/samples"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")


def chip(lines, pins_per_side=4, sides=2, w=360, h=220, blur=1.2, noise=12, angle=0, seed=0, scale=0.9):
    rng = np.random.RandomState(seed)
    pad = 40
    W, H = w + 2 * pad, h + 2 * pad
    img = np.full((H, W, 3), 60, np.uint8)  # зелёная плата
    img[:] = (40, 90, 40)
    # выводы
    def leads_h(y0, y1):
        step = w / (pins_per_side + 1)
        for i in range(pins_per_side):
            x = int(pad + step * (i + 1))
            cv2.rectangle(img, (x - 9, y0), (x + 9, y1), (200, 200, 205), -1)
    def leads_v(x0, x1):
        step = h / (pins_per_side + 1)
        for i in range(pins_per_side):
            y = int(pad + step * (i + 1))
            cv2.rectangle(img, (x0, y - 9), (x1, y + 9), (200, 200, 205), -1)
    leads_h(pad - 30, pad)
    leads_h(pad + h, pad + h + 30)
    if sides == 4:
        leads_v(pad - 30, pad)
        leads_v(pad + w, pad + w + 30)
    cv2.rectangle(img, (pad, pad), (pad + w, pad + h), (28, 28, 30), -1)
    cv2.circle(img, (pad + 22, pad + h - 22), 8, (45, 45, 48), -1)
    y = pad + 60
    for t in lines:
        cv2.putText(img, t, (pad + 25, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (170, 170, 165), 2, cv2.LINE_AA)
        y += 50
    if angle:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
        img = cv2.warpAffine(img, m, (W, H), borderMode=cv2.BORDER_REPLICATE)
    img = cv2.GaussianBlur(img, (0, 0), blur)
    img = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    # уменьшаем — как вырезка из фото платы
    return cv2.resize(img, None, fx=0.45, fy=0.45, interpolation=cv2.INTER_AREA)


def make_pdf(path, lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 18
    c.save()


def main():
    os.makedirs(OUT, exist_ok=True)
    samples = {
        "at24c02.png": chip(["ATMEL", "24C02N", "2135"], 4, 2, seed=1),
        "stm32.png": chip(["STM32F103", "C8T6", "GH26P"], 12, 4, w=400, h=400, seed=2, scale=0.85),
        "w25q64_rot.png": chip(["W25Q64JVSIQ", "1932"], 4, 2, seed=3, angle=4),
        "lm358_180.png": cv2.rotate(chip(["LM358", "L7T3"], 4, 2, seed=4), cv2.ROTATE_180),
    }
    for name, img in samples.items():
        ok, buf = cv2.imencode(".png", img)
        buf.tofile(os.path.join(OUT, name))
    lib = os.path.join(OUT, "network_share")
    os.makedirs(lib, exist_ok=True)
    make_pdf(os.path.join(lib, "AT24C01A_02_04_08_16 Datasheet.pdf"), [
        "Atmel AT24C02 Two-wire Serial EEPROM 2K (256 x 8)",
        "Features: Internally organized 256 x 8 (2K). 2-Kbit Serial EEPROM.",
        "Two-wire serial interface. Write protect pin. 1 million write cycles.",
        "Packages: 8-lead PDIP, 8-lead JEDEC SOIC, 8-lead TSSOP, SOIC-8, TSSOP-8, PDIP-8",
        "Ordering information: AT24C02N-10SU-2.7  AT24C02-10PU",
        "Package marking: top mark 24C02N, date code YYWW",
        "The EEPROM is organized as 32 pages of 8 bytes. EEPROM data retention 100 years.",
    ])
    make_pdf(os.path.join(lib, "STM32F103x8_xB.pdf"), [
        "STMicroelectronics STM32F103x8 STM32F103xB",
        "Medium-density performance line ARM-based 32-bit MCU with 64 or 128 KB Flash,",
        "USB, CAN, 7 timers, 2 ADCs, 9 communication interfaces",
        "Memories: 64 or 128 Kbytes of Flash memory, 20 Kbytes of SRAM",
        "Packages: LQFP48 7x7, LQFP64, LQFP100, UFQFPN48, VFQFPN36, BGA100",
        "Ordering: STM32F103C8T6 LQFP-48",
    ])
    make_pdf(os.path.join(lib, "lm358.pdf"), [
        "LM358 Dual Operational Amplifier",
        "Packages: SOIC-8, PDIP-8, VSSOP-8, TSSOP-8",
        "Wide supply range 3 V to 32 V. Low input bias current.",
    ])
    print("OK:", OUT)


if __name__ == "__main__":
    sys.exit(main())
