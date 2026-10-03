# -*- coding: utf-8 -*-
"""Интерфейсы модулей.

Чтобы заменить модуль, напишите класс-наследник нужного интерфейса
и укажите его в config.json в разделе "modules", например:
    "ocr": "my_package.my_ocr:MyBetterOCR"
Остальную программу менять не нужно.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .models import (Candidate, ChipInfo, Comparison, DatasheetHit,
                     DownloadResult, ImageVariant, MemoryVerdict, OcrResult,
                     ChipReport)

ProgressFn = Callable[[str], None]


class CancelToken:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Module:
    """Базовый класс. settings — раздел config.json для модуля, ctx — общий контекст."""
    name = "module"

    def __init__(self, settings: Dict[str, Any], ctx: "Context") -> None:
        self.settings = settings or {}
        self.ctx = ctx

    def close(self) -> None:
        pass


class Enhancer(Module):
    def enhance(self, image) -> List[ImageVariant]:
        raise NotImplementedError


class OCR(Module):
    def recognize(self, variants: List[ImageVariant], progress: Optional[ProgressFn] = None,
                  original=None) -> OcrResult:
        """original — исходное фото (BGR) для провайдеров, которым улучшение мешает."""
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


class Identifier(Module):
    def identify(self, text: str, alternatives: Optional[List[str]] = None) -> List[Candidate]:
        """text — основная маркировка; alternatives — другие варианты прочтения от OCR."""
        raise NotImplementedError

    def estimate_chip(self, image) -> ChipInfo:
        return ChipInfo()


class LocalDB(Module):
    def search(self, part: str, limit: int = 20) -> List[DatasheetHit]:
        raise NotImplementedError

    def index(self, roots: List[str], progress: Optional[ProgressFn] = None,
              cancel: Optional[CancelToken] = None) -> int:
        raise NotImplementedError

    def add_to_library(self, pdf_path: str, part: str, meta: Dict[str, Any]) -> str:
        raise NotImplementedError

    def get_cached_text(self, path: str) -> Optional[str]:
        return None

    def put_cached_text(self, path: str, text: str) -> None:
        pass

    def stats(self) -> Dict[str, Any]:
        return {}


class WebSearch(Module):
    def search(self, candidates: List[Candidate], progress: Optional[ProgressFn] = None,
               cancel: Optional[CancelToken] = None, levels: Optional[List[str]] = None) -> List[DatasheetHit]:
        raise NotImplementedError

    def download(self, hit: DatasheetHit, progress: Optional[ProgressFn] = None) -> DownloadResult:
        raise NotImplementedError

    def diagnose(self, progress: Optional[ProgressFn] = None,
                 cancel: Optional[CancelToken] = None) -> List[Dict[str, Any]]:
        return []


class PdfText(Module):
    def extract(self, path: str, max_pages: int = 40) -> str:
        raise NotImplementedError


class Comparator(Module):
    def compare(self, part: str, marking: str, chip: ChipInfo, datasheet_text: str) -> Comparison:
        raise NotImplementedError


class MemoryAnalyzer(Module):
    def analyze(self, part: str, datasheet_text: str, description: str = "") -> MemoryVerdict:
        raise NotImplementedError


class Reporter(Module):
    def render(self, report: ChipReport, variants: Optional[List[ImageVariant]] = None) -> str:
        raise NotImplementedError


INTERFACES = {
    "enhancer": Enhancer,
    "ocr": OCR,
    "identifier": Identifier,
    "local_db": LocalDB,
    "web_search": WebSearch,
    "pdf_text": PdfText,
    "comparator": Comparator,
    "memory": MemoryAnalyzer,
    "report": Reporter,
}


class Context:
    """Общий контекст: конфигурация, пути, журнал. Передаётся всем модулям."""

    def __init__(self, config: Dict[str, Any], app_dir: str, logger) -> None:
        self.config = config
        self.app_dir = app_dir
        self.log = logger
        self.modules: Dict[str, Module] = {}

    def module(self, role: str):
        return self.modules.get(role)
