# -*- coding: utf-8 -*-
"""Модуль распознавания маркировки через Tesseract (работает без интернета).

Как работает:
 1. Определяет поворот (0/90/180/270) по лучшей уверенности распознавания.
 2. Распознаёт все варианты изображения в двух режимах разметки страницы.
 3. Слова, которые совпали в нескольких вариантах, получают больший вес.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..core.interfaces import OCR, ProgressFn
from ..core.models import ImageVariant, OcrLine, OcrResult
from .enhance_opencv import rotate

try:
    import pytesseract
except ImportError:
    pytesseract = None

WIN_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
]

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./+#"
LOWER = "abcdefghijklmnopqrstuvwxyz"


def _alnum(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", s))


class TesseractOCR(OCR):
    name = "tesseract"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        self._ready = False
        self._error = ""
        self._setup()

    def _setup(self) -> None:
        if pytesseract is None:
            self._error = "Не установлен пакет pytesseract"
            return
        cmd = self.settings.get("tesseract_cmd", "")
        candidates = [cmd] if cmd else []
        candidates += WIN_PATHS
        for c in candidates:
            if c and os.path.isfile(c):
                pytesseract.pytesseract.tesseract_cmd = c
                break
        # tessdata рядом с программой (можно положить свои обученные модели)
        local_tessdata = os.path.join(self.ctx.app_dir, "tessdata")
        self._tessdata = local_tessdata if os.path.isdir(local_tessdata) else ""
        try:
            pytesseract.get_tesseract_version()
            self._ready = True
        except Exception as e:  # noqa
            self._error = ("Tesseract не найден. Установите его или укажите путь к tesseract.exe "
                           "в настройках. (%s)" % e)

    def is_available(self) -> bool:
        return self._ready

    @property
    def error(self) -> str:
        return self._error

    def _config(self, psm: int) -> str:
        wl = UPPER + (LOWER if self.settings.get("allow_lowercase", False) else "")
        cfg = "--oem 1 --psm %d -c tessedit_char_whitelist=%s" % (psm, wl)
        if self._tessdata:
            cfg += ' --tessdata-dir "%s"' % self._tessdata
        return cfg

    def _run(self, img, psm: int) -> List[Tuple[str, float]]:
        """Возвращает строки [(текст, средняя_уверенность)]."""
        lang = self.settings.get("lang", "eng")
        data = pytesseract.image_to_data(img, lang=lang, config=self._config(psm),
                                         output_type=pytesseract.Output.DICT)
        lines: Dict[Tuple[int, int, int], List[Tuple[str, float]]] = defaultdict(list)
        for i, word in enumerate(data["text"]):
            word = (word or "").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if not word or conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines[key].append((word, conf))
        out = []
        for key in sorted(lines):
            words = lines[key]
            text = " ".join(w for w, _ in words)
            conf = sum(c * max(1, _alnum(w)) for w, c in words) / max(1, sum(max(1, _alnum(w)) for w, _ in words))
            out.append((text, conf))
        return out

    @staticmethod
    def _score(lines: List[Tuple[str, float]]) -> float:
        """Оценка результата: награждаем слова, похожие на маркировку (буквы+цифры,
        3+ символа, высокая уверенность), штрафуем мусор из 1-2 символов."""
        s = 0.0
        for text, conf in lines:
            for w in text.split():
                n = _alnum(w)
                if n >= 3 and conf >= 40:
                    k = 1.5 if (re.search(r"[A-Za-z]", w) and re.search(r"\d", w)) else 1.0
                    s += conf * n * k
                elif n >= 3:
                    s += conf * n * 0.3
                else:
                    s -= 15
        return s

    def recognize(self, variants: List[ImageVariant], progress: Optional[ProgressFn] = None) -> OcrResult:
        if not self._ready:
            raise RuntimeError(self._error)
        if not variants:
            return OcrResult()
        say = progress or (lambda m: None)

        # 1. Поворот — по варианту с контрастом (или первому)
        probe = next((v for v in variants if v.name == "clahe"), variants[0])
        rotations = self.settings.get("rotations", [0, 90, 180, 270])
        best_rot, best = 0, -1.0
        for rot in rotations:
            say("OCR: пробую поворот %d°" % rot)
            sc = self._score(self._run(rotate(probe.image, rot), 6))
            if sc > best:
                best_rot, best = rot, sc

        # 2. Все варианты при найденном повороте
        psms = self.settings.get("psm_modes", [6, 11])
        runs = []
        result = OcrResult()
        for v in variants:
            img = rotate(v.image, best_rot)
            for psm in psms:
                say("OCR: %s, режим %d" % (v.name, psm))
                try:
                    lines = self._run(img, psm)
                except Exception as e:  # noqa
                    self.ctx.log.warning("OCR ошибка на %s: %s", v.name, e)
                    continue
                name = "%s_rot%d_psm%d" % (v.name, best_rot, psm)
                runs.append((self._score(lines), name, lines))
                for text, conf in lines:
                    result.lines.append(OcrLine(text=text, confidence=conf, variant=name))

        if not runs:
            return result

        # 3. Голосование: слово, встреченное во многих вариантах, надёжнее.
        votes: Dict[str, float] = defaultdict(float)
        for _sc, _name, lines in runs:
            for text, conf in lines:
                for w in text.split():
                    if _alnum(w) >= 2:
                        votes[w.upper()] += conf / 100.0

        def run_rank(r):
            sc, _n, lines = r
            agree = sum(votes.get(w.upper(), 0) for t, _c in lines for w in t.split())
            return sc + agree * 20

        runs.sort(key=run_rank, reverse=True)
        best_score, best_name, best_lines = runs[0]
        good = [t for t, c in best_lines if c >= float(self.settings.get("min_line_conf", 30)) and _alnum(t) >= 2]
        result.best_text = "\n".join(good) if good else "\n".join(t for t, _ in best_lines)
        result.best_variant = best_name
        return result
