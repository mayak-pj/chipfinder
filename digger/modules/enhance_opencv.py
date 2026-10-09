# -*- coding: utf-8 -*-
"""Модуль улучшения изображения (OpenCV).

Делает несколько вариантов одной картинки: разные контрасты, бинаризации,
подавление шума, выделение гравировки. OCR-модуль сам выберет, на каком
варианте текст читается лучше.
"""
from __future__ import annotations

from typing import List

import cv2
import numpy as np

from ..core.interfaces import Enhancer
from ..core.models import ImageVariant


def rotate(img, deg: int):
    if deg % 360 == 0:
        return img
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[deg % 360]
    return cv2.rotate(img, code)


def to_dark_on_light(gray):
    """Tesseract лучше читает тёмный текст на светлом фоне. Маркировка на чипах
    обычно светлая на тёмном корпусе — такие картинки инвертируем."""
    return 255 - gray if np.median(gray) < 110 else gray


def deskew(gray, max_angle: float = 12.0):
    """Небольшой наклон: ищем угол, при котором строки текста дают самые резкие
    горизонтальные полосы (максимум дисперсии суммы по строкам)."""
    h, w = gray.shape[:2]
    small_scale = 400.0 / max(h, w) if max(h, w) > 400 else 1.0
    small = cv2.resize(gray, None, fx=small_scale, fy=small_scale) if small_scale < 1 else gray
    bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    if np.mean(bw) > 127:
        bw = 255 - bw
    best, best_score = 0.0, -1.0
    center = (bw.shape[1] / 2, bw.shape[0] / 2)
    for ang in np.arange(-max_angle, max_angle + 0.1, 1.0):
        m = cv2.getRotationMatrix2D(center, ang, 1.0)
        r = cv2.warpAffine(bw, m, (bw.shape[1], bw.shape[0]), flags=cv2.INTER_NEAREST)
        score = float(np.var(r.sum(axis=1)))
        if score > best_score:
            best, best_score = float(ang), score
    if abs(best) < 0.5:
        return gray, 0.0
    m = cv2.getRotationMatrix2D((w / 2, h / 2), best, 1.0)
    out = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, best


def find_body(gray):
    """Ищет корпус микросхемы: самую крупную тёмную почти прямоугольную область.
    Возвращает (x, y, w, h) или None. Порогов два: сначала отделяем светлые
    выводы, затем внутри тёмной части отделяем корпус от платы."""
    h, w = gray.shape[:2]
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    t1 = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
    thresholds = [t1]
    low = blur[blur < t1]
    if low.size > 0.2 * h * w and low.max() > low.min() + 10:
        t2 = cv2.threshold(low.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0]
        thresholds.insert(0, t2)
    k = max(5, (min(h, w) // 12) | 1)
    best = None
    for t in thresholds:
        mask = (blur < t).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        x, y, bw, bh = cv2.boundingRect(c)
        area_ratio = bw * bh / float(h * w)
        rect = cv2.contourArea(c) / float(max(1, bw * bh))
        if 0.15 <= area_ratio <= 0.97 and rect >= 0.7:
            if best is None or rect > best[0] + 0.05:
                best = (rect, (x, y, bw, bh))
    return best[1] if best else None


class OpenCVEnhancer(Enhancer):
    name = "opencv"

    def enhance(self, image) -> List[ImageVariant]:
        s = self.settings
        target_h = int(s.get("target_height", 500))
        max_scale = float(s.get("max_upscale", 5.0))

        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        # Обрезаем до корпуса: выводы и плата вокруг мешают распознаванию.
        if s.get("crop_body", True):
            box = find_body(gray)
            if box:
                x, y, bw, bh = box
                m = 3
                gray = gray[max(0, y + m):y + bh - m, max(0, x + m):x + bw - m]

        h, w = gray.shape[:2]
        # Масштаб: мелкие вырезки увеличиваем, чтобы высота букв была ~30+ пикселей.
        scale = min(max_scale, max(1.0, target_h / float(min(h, w))))
        if scale > 1.01:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        if s.get("deskew", True):
            gray, _ang = deskew(gray)

        variants: List[ImageVariant] = []

        def add(name, img):
            variants.append(ImageVariant(name=name, image=img))

        add("gray", to_dark_on_light(gray))

        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
        add("clahe", to_dark_on_light(clahe))

        den = cv2.fastNlMeansDenoising(clahe, None, h=12, templateWindowSize=7, searchWindowSize=21)
        blur = cv2.GaussianBlur(den, (0, 0), 3)
        sharp = cv2.addWeighted(den, 1.8, blur, -0.8, 0)
        add("denoise_sharp", to_dark_on_light(sharp))

        # Гравировка: светлый тонкий текст на тёмном корпусе — top-hat его выделяет,
        # тёмный текст на светлом — black-hat.
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
        tophat = cv2.morphologyEx(den, cv2.MORPH_TOPHAT, k)
        blackhat = cv2.morphologyEx(den, cv2.MORPH_BLACKHAT, k)
        th = cv2.normalize(tophat if tophat.mean() >= blackhat.mean() else blackhat,
                           None, 0, 255, cv2.NORM_MINMAX)
        add("engraving", 255 - th)

        otsu = cv2.threshold(den, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        add("otsu", to_dark_on_light(otsu))

        block = max(15, (min(den.shape[:2]) // 12) | 1)
        adapt = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, block, 5)
        adapt = cv2.medianBlur(adapt, 3)
        add("adaptive", to_dark_on_light(adapt))

        eng_bin = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        eng_bin = cv2.morphologyEx(eng_bin, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
        add("engraving_bin", 255 - eng_bin)

        # Белая рамка — Tesseract плохо читает текст вплотную к краю.
        for v in variants:
            v.image = cv2.copyMakeBorder(v.image, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)

        enabled = s.get("variants")
        if enabled:
            variants = [v for v in variants if v.name in enabled]
        return variants
