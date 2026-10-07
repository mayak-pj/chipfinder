# -*- coding: utf-8 -*-
"""HTML-отчёт по одному чипу (открывается в окне программы и сохраняется в файл)."""
from __future__ import annotations

import base64
import html
import os
from typing import List, Optional

import cv2

from ..core.interfaces import Reporter
from ..core.models import ChipReport, ImageVariant
from ..core.utils import imread

STATUS = {"ok": ("✔", "#1a7f37"), "fail": ("✘", "#c62828"), "warn": ("!", "#b26a00"), "unknown": ("?", "#666")}
LEVEL_RU = {"local": "Локальная база", "catalog": "Каталог datasheet", "maker": "Производитель",
            "china": "Китай", "forum": "Форум", "marking": "SMD-коды", "github": "GitHub"}


def _img_tag(img, max_w=420) -> str:
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)))
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return ""
    return '<img src="data:image/png;base64,%s">' % base64.b64encode(buf.tobytes()).decode()


OCR_STATUS = {"ok": "прочитано", "weak": "низкая уверенность", "unconfirmed": "не подтверждено справочником",
              "empty": "ничего не прочитано", "failed": "ошибка", "unavailable": "недоступен",
              "no_consent": "нет согласия на отправку фото"}


def _ocr_how(ocr) -> List[str]:
    """Каким способом прочитано; если пробовали несколько способов — таблица попыток."""
    if not ocr.provider:
        return []
    out = ["<div>Способ распознавания: <b>%s</b> <span class='muted'>(уверенность %d%%, %.1f с%s)</span></div>"
           % (e(ocr.provider_title or ocr.provider), int(round(ocr.confidence)), ocr.seconds,
              "; режим «Сравнить все»" if ocr.mode == "compare" else "")]
    if len(ocr.attempts) > 1:
        out.append("<table><tr><th>Способ</th><th>Итог</th><th>Уверенность</th><th>Время</th><th>Прочитано</th></tr>")
        for a in ocr.attempts:
            ran = a.status in ("ok", "weak", "unconfirmed", "empty")
            name = e(a.title or a.provider)
            out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                "<b>%s</b>" % name if a.provider == ocr.provider else name,
                e(OCR_STATUS.get(a.status, a.status) + (": " + a.detail if a.detail else "")),
                "%d%%" % int(round(a.confidence)) if ran and a.text else "—",
                "%.1f с" % a.seconds if ran or a.status == "failed" else "—",
                e(a.text).replace("\n", " / ") or "—"))
        out.append("</table>")
    return out


def e(s) -> str:
    return html.escape(str(s or ""))


class HtmlReport(Reporter):
    name = "html"

    def render(self, r: ChipReport, variants: Optional[List[ImageVariant]] = None) -> str:
        out = ["""<html><head><meta charset="utf-8"><style>
body{font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#222;margin:12px}
h2{margin:6px 0 10px;font-size:18px} h3{margin:16px 0 6px;font-size:15px;border-bottom:1px solid #ddd}
table{border-collapse:collapse} td,th{border:1px solid #ddd;padding:4px 7px;vertical-align:top;text-align:left}
th{background:#f3f3f3} .big{font-size:16px;font-weight:bold;padding:8px;border-radius:4px}
.yes{background:#fde8e8;color:#8a1c1c} .no{background:#e8f5e9;color:#1b5e20} .unk{background:#eee}
.muted{color:#777} img{border:1px solid #ccc;margin:4px}
</style></head><body>"""]
        out.append("<h2>Чип: %s</h2>" % e(r.chosen_part or "не определён"))
        out.append('<div class="muted">Фото: %s</div>' % e(r.image_path))

        # --- Память ---
        if r.memory:
            cls = "yes" if r.memory.has_memory else ("no" if r.memory.has_memory is False else "unk")
            out.append("<h3>Память</h3>")
            out.append('<div class="big %s">%s <span class="muted">(уверенность %d%%)</span></div>'
                       % (cls, e(r.memory.summary), int(r.memory.confidence * 100)))
            if r.memory.items:
                out.append("<table><tr><th>Тип</th><th>Объём</th><th>Основание</th></tr>")
                for it in r.memory.items:
                    out.append("<tr><td>%s</td><td>%s</td><td class='muted'>%s</td></tr>"
                               % (e(it.kind), e(it.size or "—"), e(it.evidence)))
                out.append("</table>")

        # --- Сверка ---
        if r.comparison:
            c = r.comparison
            out.append("<h3>Сверка фото и datasheet: %s (%d%%)</h3>" % (e(c.verdict), int(c.score * 100)))
            out.append('<div class="muted">Файл: %s</div>' % e(r.datasheet_path))
            out.append("<table><tr><th></th><th>Проверка</th><th>Результат</th></tr>")
            for ch in c.checks:
                sym, col = STATUS.get(ch.status, STATUS["unknown"])
                out.append("<tr><td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td></tr>"
                           % (col, sym, e(ch.name), e(ch.detail)))
            out.append("</table>")

        # --- Распознавание ---
        out.append("<h3>Распознавание</h3>")
        if r.ocr:
            out.append("<div>Маркировка: <b>%s</b> <span class='muted'>(лучший вариант: %s)</span></div>"
                       % (e(r.ocr.best_text).replace("\n", " / "), e(r.ocr.best_variant)))
            out.extend(_ocr_how(r.ocr))
        chip = r.chip
        out.append("<div>Корпус на фото: %s; выводов: %s%s; соотношение сторон: %s</div>" % (
            e(chip.package or "не указан"), chip.pins or "?", " (оценка)" if chip.pins_estimated else "",
            chip.body_ratio or "?"))
        if r.candidates:
            out.append("<table><tr><th>Кандидат</th><th>Вероятность</th><th>Семейство / описание</th><th>Почему</th></tr>")
            for cd in r.candidates[:10]:
                out.append("<tr><td><b>%s</b></td><td>%d%%</td><td>%s</td><td class='muted'>%s</td></tr>"
                           % (e(cd.part), int(cd.score * 100), e(cd.description), e(cd.reason)))
            out.append("</table>")
        imgs = []
        if r.image_path and os.path.exists(r.image_path):
            try:
                imgs.append(_img_tag(imread(r.image_path), 300))
            except Exception:  # noqa
                pass
        best = r.ocr.best_variant.split("_rot")[0] if r.ocr and r.ocr.best_variant else ""
        for v in variants or []:
            if v.name == best:
                imgs.append(_img_tag(v.image, 420))
        if imgs:
            out.append("<div>%s</div>" % "".join(imgs))

        # --- Найденные документы ---
        if r.hits:
            out.append("<h3>Найденные документы (%d)</h3>" % len(r.hits))
            out.append("<table><tr><th>Где</th><th>Источник</th><th>Название</th><th>Оценка</th><th>Адрес</th></tr>")
            for h in r.hits[:40]:
                flag = "" if h.allowed else " <span style='color:#b26a00'>(вне белого списка)</span>"
                out.append("<tr><td>%s</td><td>%s</td><td>%s%s</td><td>%d%%</td><td style='word-break:break-all'>%s</td></tr>"
                           % (e(LEVEL_RU.get(h.level, h.level)), e(h.source), e(h.title), flag,
                              int(h.score * 100), e(h.location)))
            out.append("</table>")
        if r.log:
            out.append("<h3>Журнал</h3><pre class='muted' style='white-space:pre-wrap'>%s</pre>"
                       % e("\n".join(r.log[-60:])))
        out.append("</body></html>")
        return "\n".join(out)
