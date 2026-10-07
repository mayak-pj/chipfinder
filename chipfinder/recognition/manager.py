# -*- coding: utf-8 -*-
"""Менеджер провайдеров распознавания — реализация роли `ocr`.

Цепочка из `config → recognition.chain`: следующий провайдер пробуется, если текущий недоступен,
упал или ничего не прочитал. О конкретных провайдерах менеджер не знает — встроенные находит в
`recognition/providers/`, сторонние — в `plugins/ocr_<имя>/` (provider.json + provider.py).
Результат несёт список попыток (`OcrResult.attempts`) и строки всех отработавших провайдеров;
время и исходы копятся в `RecognitionManager.stats`.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import read_json
from ..core.interfaces import OCR, ProgressFn
from ..core.models import ImageVariant, OcrAttempt, OcrResult
from . import providers as builtin_package
from .api import OcrProvider

DEFAULT_CHAIN = ["ppocr", "tesseract"]
PLUGIN_PREFIX = "ocr_"
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
KINDS = ("local", "cloud")


@dataclass
class ProviderInfo:
    id: str
    title: str = ""
    kind: str = "local"       # local | cloud
    builtin: bool = False
    path: str = ""            # папка стороннего провайдера
    error: str = ""           # почему не загрузился


def builtin_ids() -> List[str]:
    return sorted(m.name for m in pkgutil.iter_modules(builtin_package.__path__) if m.ispkg)


def discover_plugins(plugins_dir: str) -> List[ProviderInfo]:
    """Папки `ocr_*` с provider.json. Код провайдера здесь не выполняется — только описание."""
    found: List[ProviderInfo] = []
    if not os.path.isdir(plugins_dir):
        return found
    for name in sorted(os.listdir(plugins_dir)):
        path = os.path.join(plugins_dir, name)
        manifest = os.path.join(path, "provider.json")
        if not name.startswith(PLUGIN_PREFIX) or not os.path.isfile(manifest):
            continue
        info = ProviderInfo(id=name[len(PLUGIN_PREFIX):], path=path)
        try:
            data = read_json(manifest)
            info.id = str(data.get("id") or info.id)
            info.title = str(data.get("title") or info.id)
            info.kind = str(data.get("kind") or "local")
            if not ID_RE.match(info.id):
                raise ValueError("id «%s»: нужны латинские строчные буквы, цифры и _" % info.id)
            if info.kind not in KINDS:
                raise ValueError("kind «%s»: нужно local или cloud" % info.kind)
        except Exception as e:  # noqa
            info.error = "provider.json: %s" % e
        found.append(info)
    return found


def load_provider_class(info: ProviderInfo):
    """Класс `Provider(OcrProvider)` встроенного провайдера или `provider.py` из папки стороннего."""
    if info.builtin:
        where = "chipfinder.recognition.providers.%s" % info.id
        mod = importlib.import_module(where)
    else:
        where = os.path.join(info.path, "provider.py")
        name = "chipfinder_ocr_" + info.id
        spec = importlib.util.spec_from_file_location(name, where)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    cls = getattr(mod, "Provider", None)
    if not (isinstance(cls, type) and issubclass(cls, OcrProvider)):
        raise ImportError("в %s нет класса Provider(OcrProvider)" % where)
    return cls


class RecognitionManager(OCR):
    name = "recognition"

    def __init__(self, settings, ctx):
        super().__init__(settings, ctx)
        cfg = ctx.config.get("recognition", {})
        self.chain: List[str] = list(cfg.get("chain") or DEFAULT_CHAIN)
        self.known: Dict[str, ProviderInfo] = {}
        self.providers: Dict[str, OcrProvider] = {}
        self.problems: Dict[str, str] = {}      # id → почему не работает
        self.stats: Dict[str, Dict[str, float]] = {}   # id → calls / ok / empty / failed / seconds
        # согласие на отправку фото облачному провайдеру; пока его некому дать — облако не вызывается
        self.cloud_consent: Callable[[OcrProvider], bool] = lambda provider: False
        self._lock = threading.Lock()
        self._discover(os.path.join(ctx.app_dir, "plugins"))
        for pid in self.chain:
            info = self.known.get(pid)
            if info is None:
                self._broken(pid, "нет такого провайдера (ни встроенного, ни plugins/%s%s)" % (PLUGIN_PREFIX, pid))
            elif info.error:
                self._broken(pid, info.error)
            else:
                self._create(info, cfg.get("providers", {}).get(pid, {}))

    # -------------------- поиск и загрузка --------------------
    def _discover(self, plugins_dir: str) -> None:
        for pid in builtin_ids():
            self.known[pid] = ProviderInfo(id=pid, title=pid, builtin=True)
        for info in discover_plugins(plugins_dir):
            if info.id in self.known:
                self.ctx.log.warning("Провайдер распознавания из %s пропущен: id «%s» уже занят", info.path, info.id)
                continue
            self.known[info.id] = info
            if info.error:
                self.ctx.log.warning("Провайдер распознавания в %s: %s", info.path, info.error)

    def _create(self, info: ProviderInfo, settings: Dict[str, Any]) -> None:
        try:
            cls = load_provider_class(info)
            self.providers[info.id] = cls(settings, self.ctx)
        except Exception as e:  # noqa — сломанный провайдер не должен ронять программу
            info.error = "не загрузился: %s: %s" % (type(e).__name__, e)
            self._broken(info.id, info.error)
            return
        if info.builtin or not info.title:
            info.title = cls.title
        if cls.kind == "cloud":         # облаком считается, если так сказано хоть где-то
            info.kind = "cloud"

    def _broken(self, pid: str, why: str) -> None:
        self.problems[pid] = why
        self.ctx.log.warning("Провайдер распознавания %s: %s", pid, why)

    def catalog(self) -> List[ProviderInfo]:
        """Все известные провайдеры: сначала цепочка по порядку, потом остальные."""
        rest = [i for pid, i in sorted(self.known.items()) if pid not in self.chain]
        return [self.known[pid] for pid in self.chain if pid in self.known] + rest

    # -------------------- доступность --------------------
    def _title(self, pid: str) -> str:
        info = self.known.get(pid)
        return (info.title if info else "") or pid

    def _available(self, pid: str) -> Tuple[bool, str]:
        p = self.providers.get(pid)
        if p is None:
            return False, self.problems.get(pid, "нет такого провайдера")
        if self.known[pid].kind == "cloud" and not self.cloud_consent(p):
            ok, why = False, "облачный провайдер: нет согласия пользователя на отправку фото"
        else:
            try:
                ok, why = p.is_available()
            except Exception as e:  # noqa
                ok, why = False, str(e)
        if not ok:
            self.problems[pid] = why
        return ok, why

    def is_available(self) -> bool:
        return any(self._available(pid)[0] for pid in self.chain)

    @property
    def error(self) -> str:
        return "; ".join("%s — %s" % (pid, self.problems[pid]) for pid in self.chain if pid in self.problems)

    # -------------------- распознавание --------------------
    def _count(self, attempt: OcrAttempt) -> None:
        with self._lock:
            st = self.stats.setdefault(attempt.provider, {"calls": 0, "ok": 0, "empty": 0, "failed": 0, "seconds": 0.0})
            st["calls"] += 1
            st[attempt.status] += 1
            st["seconds"] = round(st["seconds"] + attempt.seconds, 3)

    def recognize(self, variants: List[ImageVariant], progress: Optional[ProgressFn] = None,
                  original=None) -> OcrResult:
        say = progress or (lambda m: None)
        attempts: List[OcrAttempt] = []
        done: List[OcrResult] = []      # результаты отработавших провайдеров, по порядку цепочки
        for pid in self.chain:
            ok, why = self._available(pid)
            if not ok:
                self.ctx.log.info("Распознавание: %s недоступен (%s)", pid, why)
                attempts.append(OcrAttempt(pid, self._title(pid), "unavailable", detail=why))
                continue
            p = self.providers[pid]
            imgs = variants
            if p.wants_original and original is not None:
                imgs = [ImageVariant(name="original", image=original)]
            say("Распознаю: %s" % self._title(pid))
            attempt = OcrAttempt(pid, self._title(pid))
            attempts.append(attempt)
            t0 = time.perf_counter()
            try:
                res = p.recognize(imgs, hints={}, progress=progress)
            except Exception as e:  # noqa — упал → следующий в цепочке
                attempt.status, attempt.detail = "failed", "%s: %s" % (type(e).__name__, e)
                attempt.seconds = round(time.perf_counter() - t0, 3)
                self.problems[pid] = "ошибка: %s" % e
                self.ctx.log.exception("Провайдер распознавания %s упал", pid)
                self._count(attempt)
                continue
            attempt.seconds = round(time.perf_counter() - t0, 3)
            attempt.lines = len(res.lines)
            attempt.status = "ok" if res.best_text.strip() else "empty"
            self._count(attempt)
            res.provider, res.provider_title, res.seconds = pid, attempt.title, attempt.seconds
            for line in res.lines:
                line.provider = pid
            self.ctx.log.info("Распознавание: %s, %.2f с, строк %d", pid, res.seconds, len(res.lines))
            done.append(res)
            if attempt.status == "ok":
                break
        return self._merge(done, attempts)

    @staticmethod
    def _merge(done: List[OcrResult], attempts: List[OcrAttempt]) -> OcrResult:
        """Итог — результат последнего отработавшего провайдера (он либо прочитал, либо цепочка кончилась);
        строки остальных добавляются после его строк — как запасные варианты для выбора партномера."""
        result = done[-1] if done else OcrResult()
        seen = {line.text.strip().upper() for line in result.lines}
        for other in done[:-1]:
            for line in other.lines:
                key = line.text.strip().upper()
                if key and key not in seen:
                    seen.add(key)
                    result.lines.append(line)
        result.attempts = attempts
        result.total_seconds = round(sum(a.seconds for a in attempts), 3)
        return result

    def close(self) -> None:
        for p in self.providers.values():
            try:
                p.close()
            except Exception:  # noqa
                pass
